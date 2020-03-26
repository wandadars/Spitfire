"""
This module defines containers for tabulated chemistry libraries and solution trajectories
"""

# Spitfire - a Python-C++ library for building tabulated chemistry models and solving differential equations
# Copyright 2019 National Technology & Engineering Solutions of Sandia, LLC (NTESS).
#
# You should have received a copy of the 3-clause BSD License
# along with this program.  If not, see <https://opensource.org/licenses/BSD-3-Clause>.
#
# Questions? Contact Mike Hansen (mahanse@sandia.gov)


import numpy as np
import pickle as pickle
from copy import copy, deepcopy
import shutil
import os


class Dimension(object):
    """A class to contain details of a particular independent variable in a structured or unstructured library

    **Constructor**: specify a name, values, and whether or not the dimension is structured

    Parameters
    ----------
    name : str
        the name of the mechanism - hyphens and spaces may not be used here, use underscore separators
    values: np.array
        the values of the independent variable in the grid
    structured: bool
        whether or not the dimension is structured (optional, default: True)
    """

    def __init__(self, name: str, values: np.array, structured=True, _library_owned=False):
        self._name = name
        self._values = np.copy(values)
        self._min = np.min(values)
        self._max = np.max(values)
        self._npts = values.size
        self._structured = structured
        self._grid = None
        self._library_owned = _library_owned

        if not name.isidentifier():
            raise ValueError(f'Error in building Dimension "{name}", the name cannot contain hyphens or spaces '
                             f'(it must be a valid Python variable name, note that you can check this with name.isidentifier())')

        if len(self._values.shape) != 1:
            raise ValueError(f'Error in building Dimension "{name}", the values object must be one-dimensional.'
                             f' Call the ravel() method to flatten your data (without a deep copy).')

        if structured:
            if self._values.size != np.unique(self._values).size:
                raise ValueError(f'Error in building structured dimension "{name}"'
                                 ', duplicate values identified!')

    def __str__(self):
        s = 'Structured' if self._structured else 'Unstructured'
        return f'{s} dimension "{self._name}" spanning [{self._min}, {self._max}] with {self._npts} points'

    def __repr__(self):
        s = 'Structured' if self._structured else 'Unstructured'
        return f'Spitfire Dimension(name="{self._name}", min={self._min}, max={self._max}, npts={self._npts}, {s})'

    @property
    def name(self):
        """Obtain the name of the independent variable"""
        return self._name

    @property
    def values(self):
        """Obtain the one-dimensional np.array of the specified values of this independent variable"""
        return self._values

    @property
    def min(self):
        return self._min

    @property
    def max(self):
        return self._max

    @property
    def npts(self):
        return self._npts

    @property
    def structured(self):
        return self._structured

    @property
    def grid(self):
        """Obtain the np.ndarray "meshgrid" of the values of this variable in a multi-dimensional library
        Upon construction of the Dimension instance, this returns the given one-dimensional values,
        but after incorporation of the Dimension into a library, this is made into a meshgrid object.
        Note that the library object will always copy Dimension data into a brand new instance,
        so the Dimension instances fed to a Library are not modified. This is consistent in that a Dimension instance
        does not have multidimensional grid data unless incorporated into a multidimensional Library. In the case
        of unstructured data, the grid is always equivalent to the values and consistency is implicit."""
        return self._values if self._grid is None else self._grid

    @grid.setter
    def grid(self, grid):
        """Set the meshgrid object - do not call explicitly, this can only be called by the Library class
        that owns the Dimension."""
        if self._library_owned:
            self._grid = grid
        else:
            raise ValueError(f'Explicitly setting the "grid" property on Dimension "{self._name}" is not allowed.'
                             f'Only an owning Library object can set the multidimensional grid.')

    def _get_dict_for_file_save(self):
        return {'name': self._name, 'values': self._values, 'structured': self._structured}


class LibraryIndexError(IndexError):
    pass


class Library(object):
    """A container class for tabulated datasets over structured and unstructured grids,
        althought unstructured grid support is far less advanced.

    Upon constructing the Library object, the following properties are made available for each Dimension:
      library.[dimension_name]_name
      library.[dimension_name]_values
      library.[dimension_name]_min
      library.[dimension_name]_max
      library.[dimension_name]_npts
      library.[dimension_name]_structured
      library.[dimension_name]_grid

    **Constructor**: specify the argument list of dimensions defining the grid

    Parameters
    ----------
    dimensions : argument list of Dimension instances
        the dimensions that define the grid
    """

    def __init__(self, *dimensions):
        self._dims = dict({d.name: Dimension(d.name, d.values, d.structured, _library_owned=True) for d in dimensions})
        self._props = dict()
        self._dims_ordering = dict()
        for i, d in enumerate(dimensions):
            self._dims_ordering[i] = d.name

        self._structured = all([self._dims[d].structured for d in self._dims])
        unstructured = all([not self._dims[d].structured for d in self._dims])
        if not self._structured and not unstructured:
            raise ValueError(
                'Error in building Library - Dimensions must be either all structured or all unstructured!')

        if len(dimensions):
            if self._structured:
                grid = np.meshgrid(*[self._dims[d].values for d in self._dims], indexing='ij')
                self._grid_shape = grid[0].shape
                for i, d in enumerate(self._dims):
                    self._dims[d].grid = grid[i]
            else:
                dim0 = self._dims[next(self._dims.keys())]
                self._grid_shape = dim0.grid.shape
                if not all([self._dims[d].grid.shape == self._grid_shape for d in self._dims]):
                    raise ValueError('Unstructured dimensions did not have the same grid shape!')

                if not all([len(self._dims[d].grid.shape) == 1 for d in self._dims]):
                    raise ValueError('Unstructured dimensions were not given as flat arrays!')

            for d in self._dims:
                setattr(self, self._dims[d].name, d)
                for a in self._dims[d].__dict__:
                    if a is not '_library_owned':
                        setattr(self, self._dims[d].name + a, getattr(self._dims[d], a))

        self._extra_attributes = dict()

    @property
    def extra_attributes(self):
        """Get the extra attributes dictionary of this Library,
            which will be saved in the instance and in any pickle or text files.
            Use this to retain any random information that you might like later,
            such as authorship notes, dates, recommendations for use, etc."""
        return self._extra_attributes

    def __getstate__(self):
        return dict(dimensions={d: self._dims[d]._get_dict_for_file_save() for d in self._dims},
                    dim_ordering=self._dims_ordering,
                    properties=self._props,
                    extra_attributes=self._extra_attributes)

    def __setstate__(self, instance_dict):
        ordered_dims = list([None] * len(instance_dict['dimensions'].keys()))
        for index in instance_dict['dim_ordering']:
            name = instance_dict['dim_ordering'][index]
            d = instance_dict['dimensions'][name]
            ordered_dims[index] = Dimension(d['name'], d['values'], d['structured'])
        self.__init__(*ordered_dims)
        for prop in instance_dict['properties']:
            self[prop] = instance_dict['properties'][prop]
        ea = dict() if 'extra_attributes' not in instance_dict else instance_dict['extra_attributes']
        for p in ea:
            self.extra_attributes[p] = ea[p]

    def save_to_file(self, file_name):
        """Save a library to a specified file using pickle"""
        with open(file_name, 'wb') as file_output:
            pickle.dump(self, file_output)

    def save_to_text_directory(self, output_directory, ravel_order='F'):
        """
        Dump the contents of a library to a set of easy-to-process text files in a directory.
        Note that file names of property bulk data files will have spaces replaced by underscores.
        Note that the preferred method of saving data for later use with Spitfire is the save_to_file method,
        which dumps compressed data with pickle, Python's native serialization tool. The Library.load_from_file method
        can then be used to reload data into Python, which is significantly faster than loading from text files.
        This method of dumping data does not natively support reloading data from the text files,
        and is simply meant to provide data that is easy to load in other codes (e.g., C++, Fortran, or Matlab codes).

        :param output_directory: where to save the files (a new directory will be made, removed with permission if already exists)
        :param ravel_order: row-major ('C') or column-major ('F') flattening of multidimensional property arrays, default is 'F' for column-major,
            which flattens the first dimension first, second dimension second, and so on
        """
        out_dir_exists = os.path.isdir(output_directory)
        proceed = input(
            f'Library.save_to_text_directory(): remove existing directory {output_directory}? (y/any=no) ') if out_dir_exists else 'y'

        if proceed != 'y':
            print('Library.save_to_text_directory(): cannot override existing output directory, aborting!')
            return

        if out_dir_exists:
            shutil.rmtree(output_directory)

        os.mkdir(output_directory)

        md_iv_file_name = os.path.join(output_directory, 'metadata_independent_variables.txt')
        md_dv_file_name = os.path.join(output_directory, 'metadata_dependent_variables.txt')
        md_ea_file_name = os.path.join(output_directory, 'metadata_user_defined_attributes.txt')
        bd_prefix = 'bulkdata'

        prop_names_underscored = dict({p: p.replace(' ', '_') for p in self.props})

        with open(md_iv_file_name, 'w') as f:
            for d in self.dims:
                f.write(d.name + '\n')

        with open(md_dv_file_name, 'w') as f:
            for p in self.props:
                f.write(prop_names_underscored[p] + '\n')

        with open(md_ea_file_name, 'w') as f:
            f.write(str(self._extra_attributes))

        for d in self.dims:
            np.savetxt(os.path.join(output_directory, f'{bd_prefix}_{d.name}.txt'),
                       d.values)

        for p in self.props:
            np.savetxt(os.path.join(output_directory, f'{bd_prefix}_{prop_names_underscored[p]}.txt'),
                       self[p].ravel(order=ravel_order))

    @classmethod
    def load_from_file(cls, file_name):
        """Load a library from a specified file name with pickle (following save_to_file)"""
        with open(file_name, 'rb') as file_input:
            pickled_data = pickle.load(file_input)

        if isinstance(pickled_data, dict):  # for compatibility with v1.0 outputs that were pickled as dictionaries
            library = Library()
            library.__setstate__(pickled_data)
            return library
        else:
            return pickled_data

    def __copy__(self):
        new_dimensions = []
        for d in self.dims:
            new_d = Dimension(d.name, d.values, d.structured)
            new_dimensions.append(new_d)
        new_library = Library(*new_dimensions)
        for p in self.props:
            new_library[p] = self[p]
        return new_library

    def __deepcopy__(self, memodict={}):
        new_dimensions = []
        for d in self.dims:
            new_d = Dimension(d.name, np.copy(d.values), d.structured)
            new_dimensions.append(new_d)
        new_library = Library(*new_dimensions)
        for p in self.props:
            new_library[p] = np.copy(self[p])
        return new_library

    @classmethod
    def copy(cls, library):
        """Shallow copy of a library into a new one"""
        return copy(library)

    @classmethod
    def deepcopy(cls, library):
        """Deep copy of a library into a new one"""
        return deepcopy(library)

    @classmethod
    def squeeze(cls, library):
        """Produce a new library from another by removing dimensions with only one value,
            for instance after slicing, lib_new = Library.squeeze(library[:, 0]).
            Note that if all dimensions are removed, for instance in Library.squeeze(library[0, 1]),
            a dictionary of the scalar values in the following form is returned instead of a new library:
            {'dimensions': {name1: value1, name2: value2, ...}, 'properties': {prop1: value1, ...}}"""
        new_dimensions = []
        for d in library.dims:
            if d.values.size > 1:
                new_d = Dimension(d.name, d.values, d.structured)
                new_dimensions.append(new_d)
        if not new_dimensions:
            return dict({'dimensions': dict({p: np.squeeze(library[p]) for p in library.props}),
                         'properties': dict({d.name: np.squeeze(d.values) for d in library.dims})})
        else:
            new_library = Library(*new_dimensions)
            for p in library.props:
                new_library[p] = np.squeeze(library[p])
            return new_library

    def __setitem__(self, quantity: str, values: np.ndarray):
        """Use the bracket operator, as in lib['myprop'] = values, to add a property defined on the grid
           The np.ndarray of values must be shaped correctly"""
        if values.shape != self._grid_shape:
            raise ValueError(f'The shape of the "{quantity}" array does not conform to that of the library. '
                             f'Given shape = {values.shape}, grid shape = {self._grid_shape}')
        self._props[quantity] = np.copy(values)

    def __getitem__(self, *slices):
        """Either return the data for a property, as in lib['myprop'], when a single string is provided,
            or obtain an entirely new library that is sliced according to the arguments, as in lib[:, 1:-1, 0, :].
            Note that this will preserve the dimensionality of a library, even if a dimension has a single value.
            Use the Library.squeeze(lib) class method to remove single-value dimensions if desired."""
        arg1 = slices[0]
        if isinstance(arg1, str):
            if len(slices) == 1:
                return self._props[arg1]
            else:
                raise LibraryIndexError(f'Library[...] can either take a single string or slices, '
                                        f'you provided it {slices}')
        else:
            if not self.dims[0].structured:
                raise LibraryIndexError('Library[...] slicing is currently not supported for unstructured Libraries')
            slices = slices if isinstance(slices[0], slice) else slices[0]
            if len(slices) != len(self.dims):
                raise LibraryIndexError(
                    f'Library[...] slicing must be given the same number of arguments as there are dimensions, '
                    f'you provided {len(slices)} slices to a Library of dimension {len(self.dims)}')
            new_dimensions = []
            for d, s in zip(self.dims, slices):
                new_d = Dimension(d.name,
                                  np.array([d.values[s]]) if isinstance(d.values[s], float) else d.values[s],
                                  d.structured)
                new_dimensions.append(new_d)
            new_library = Library(*new_dimensions)
            for p in self.props:
                new_library[p] = self._props[p][slices].reshape(new_library.dims[0].grid.shape)
            return new_library

    def __contains__(self, prop):
        return prop in self._props

    def __str__(self):
        return f'\nSpitfire Library with {len(self.dims)} dimensions and {len(list(self._props.keys()))} properties\n' + \
               '------------------------------------------\n' + \
               '\n'.join([f'{i+1}. {str(d)}' for (i, d) in enumerate(self.dims)]) + \
               f'\nProperties: [{", ".join(list(self._props.keys()))}]'

    def __repr__(self):
        return f'\nSpitfire Library(ndim={len(self.dims)}, nproperties={len(list(self._props.keys()))})\n' + \
               '\n'.join([f'{i+1}. {str(d)}' for (i, d) in enumerate(self.dims)]) + \
               f'\nProperties: [{", ".join(list(self._props.keys()))}]'

    @property
    def size(self):
        return self.dims[0].grid.size

    @property
    def shape(self):
        return self.dims[0].grid.shape

    @property
    def props(self):
        """Obtain a list of the names of properties set on the library"""
        return list(self._props.keys())

    @property
    def dims(self):
        """Obtain the ordered list of the Dimension objects associated with the library"""
        dims = []
        for d in self._dims_ordering:
            dims.append(self._dims[self._dims_ordering[d]])
        return dims

    def dim(self, name):
        """Obtain a Dimension object by name"""
        return self._dims[name]

    def get_empty_dataset(self):
        """Obtain an empty dataset in the shape of the grid, to enable filling one point, line, plane, etc. at a time,
        before then possibly setting a library property with the data"""
        return np.ndarray(self._grid_shape)

    def remove(self, *quantities):
        """Remove quantities (argument list of strings) from the library"""
        for quantity in quantities:
            self._props.pop(quantity)
