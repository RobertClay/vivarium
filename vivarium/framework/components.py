"""Tools for interpreting component configuration files as well as the default ComponentManager class which uses those tools
to load and manage components.
"""

import ast
from collections import Iterable
from importlib import import_module
import inspect
from typing import Tuple, Callable, Sequence, Mapping, Union

import yaml

from vivarium import config


class ComponentConfigError(Exception):
    """Error while interpreting configuration file or initializing components
    """
    pass


class ParsingError(ComponentConfigError):
    """Error while parsing component descriptions
    """
    pass


class DummyDatasetManager:
    """Placeholder implementation of the DatasetManager
    """
    def __init__(self):
        self.constructors = {}


def _import_by_path(path: str) -> Union[type, Callable]:
    """Import a class or function given it's absolute path.

    Parameters
    ----------
    path:
      Absolute class to object to import
    """

    module_path, _, class_name = path.rpartition('.')
    return getattr(import_module(module_path), class_name)


def load_component_manager(config_source: str = None, config_path: str = None, dataset_manager_class: type = None):
    """Create a component manager along with it's dataset manager. The class used will be either the default or
    a custom class specified in the configuration.

    Parameters
    ----------
    config_source:
      The YAML source of the configuration file to use.
    config_path:
      The path to a YAML configuration file to use.
    dataset_manager_class:
      Class to use for the dataset manager. Will override dataset manager class specified in the configuration if supplied.
    """

    if sum([config_source is None, config_path is None]) != 1:
        raise ComponentConfigError('Must supply either source or path but not both')

    if config_path:
        if config_path.endswith('.yaml'):
            with open(config_path) as f:
                config_source = f.read()
        else:
            raise ComponentConfigError("Unknown components configuration type: {}".format(config_path))

    raw_config = yaml.load(config_source)

    if raw_config.get('configuration', {}).get('vivarium', {}).get('component_manager'):
        manager_class_name = raw_config['configuration']['vivarium']['component_manager']
        component_manager_class = _import_by_path(manager_class_name)
    else:
        component_manager_class = ComponentManager

    if dataset_manager_class is None:
        if raw_config.get('configuration', {}).get('vivarium', {}).get('dataset_manager'):
            manager_class_name = raw_config['configuration']['vivarium']['dataset_manager']
            dataset_manager_class = _import_by_path(manager_class_name)
        else:
            dataset_manager_class = DummyDatasetManager

    if 'configuration' in raw_config:
        config.read_dict(raw_config['configuration'], layer='model_override', source=config_path)

    manager = component_manager_class(raw_config.get('components', {}), dataset_manager_class())
    return manager


class ComponentManager:
    """ComponentManager interprets the component configuration and loads all component classes and functions while
    tracking which ones were loaded.
    """

    def __init__(self, component_config, dataset_manager):
        self.tags = {}
        self.component_config = component_config
        self.components = []
        self.dataset_manager = dataset_manager


    def load_components_from_config(self):
        """Load and initialize (if necessary) any components listed in the config and register them with the ComponentManager.
        """

        component_list = _extract_component_list(self.component_config)
        component_list = _prep_components(component_list, self.dataset_manager.constructors)

        new_components = []
        for component in component_list:
            if len(component) == 1:
                new_components.append(component[0])
            else:
                new_components.append(component[0](*component[1]))

        self.components.extend(new_components)


    def add_components(self, components: Sequence):
        """Register new components.

        Parameters
        ----------
        components:
          Components to register
        """

        self.components.extend(components)


    def setup_components(self, builder):
        """Apply component level configuration defaults to the global config and run setup methods on the components
        registering and setting up any child components generated in the process.

        Parameters
        ----------
        builder:
            Interface to several simulation tools.
        """

        done = []

        components = list(self.components)
        while components:
            component = components.pop(0)
            if component is None:
                raise ComponentConfigError('None in component list. This likely indicates a bug in a factory function')

            if isinstance(component, Iterable):
                # Unpack lists of components so their constituent components get initialized
                components.extend(component)
                self.components.extend(component)

            if component not in done:
                if hasattr(component, 'configuration_defaults'):
                    # This reapplies configuration from some components but
                    # it is idempotent so there's no effect.
                    config.read_dict(component.configuration_defaults, layer='component_configs', source=component)

                if hasattr(component, 'setup'):
                    sub_components = component.setup(builder)
                    done.append(component)
                    if sub_components:
                        components.extend(sub_components)
                        self.components.extend(sub_components)


def _extract_component_list(component_config: Mapping[str, Union[str, Mapping]]) -> Sequence[str]:
    """Extract component descriptions from the hierarchical package/module groupings in the config file.

    Parameters
    ----------
    component_config
       The configuration to read from
    """

    def _process_level(level, prefix):
        component_list = []
        for child in level:
            if isinstance(child, dict):
                for path_suffix, sub_level in child.items():
                    component_list.extend(_process_level(sub_level, prefix + [path_suffix]))
            else:
                component_list.append('.'.join(prefix + [child]))
        return component_list

    return _process_level(component_config, [])

def _component_ast_to_path(component: ast.AST) -> str:
    """Convert the AST representing a component into a string
    which can be imported.

    Parameters
    ----------
    component:
        The node representing the component
    """

    if isinstance(component, ast.Name):
        return component.id
    path = []
    current = component
    while isinstance(current, ast.Attribute):
        path.insert(0, current.attr)
        current = current.value
    path.insert(0, current.id)
    return '.'.join(path)

def _parse_component(desc: str, constructors: Mapping[str, Callable]) -> Tuple[str, Sequence]:
    """Parse a component definition in a subset of python syntax and return an importable
    path to the specified component along with the arguments it should receive when invoked.
    If the definition is not parsable a ParsingError is raised.

    If the component's arguments are not literals they are looked up in the constructors mapping
    and if a compatible constructor is present it will be called. If no matching constructor is
    available a ParsingError is raised.

    Parameters
    ----------
    desc
        The component definition
    constructors
        Dictionary of callables for creating argument objects
    """

    component, *args = ast.iter_child_nodes(list(ast.iter_child_nodes(list(ast.iter_child_nodes(ast.parse(desc)))[0]))[0])
    component = _component_ast_to_path(component)
    new_args = []
    for arg in args:
        if isinstance(arg, ast.Str):
            new_args.append(arg.s)
        elif isinstance(arg, ast.Num):
            new_args.append(arg.n)
        elif isinstance(arg, ast.Call):
            constructor, *constructor_args = ast.iter_child_nodes(arg)
            constructor = constructors.get(constructor.id)
            if constructor and len(constructor_args) == 1 and isinstance(constructor_args[0], ast.Str):
                new_args.append(constructor(constructor_args[0].s))
            else:
                raise ParsingError('Invalid syntax: {}'.format(desc))
        else:
            raise ParsingError('Invalid syntax: {}'.format(desc))
    return component, new_args

def _prep_components(component_list: Sequence, constructors: Mapping[str, Callable]) -> Sequence:
    """Transform component description strings into tuples of component callables and any arguments the component may need.

    Parameters
    ----------
    component_list
        The component descriptions to transform
    constructors
        Dictionary of callables for creating argument objects

    Returns
    -------
    List of component/argument tuples.
    """

    components = []
    for component in component_list:
        if isinstance(component, str):
            if '(' in component:
                component, args = _parse_component(component, constructors)
                call = True
            else:
                call = False

            component = _import_by_path(component)

            for attr, val in inspect.getmembers(component, lambda a: not inspect.isroutine(a)):
                constructor = constructors.get(val.__class__.__name__)
                if constructor:
                    setattr(component, attr, constructor(val.entity_path))

            # Establish the initial configuration
            if hasattr(component, 'configuration_defaults'):
                config.read_dict(component.configuration_defaults, layer='component_configs', source=component)

            if call:
                component = (component, args)
            else:
                component = (component,)

        elif isinstance(component, type):
            component = (component, tuple())

        components.append(component)

    return components
