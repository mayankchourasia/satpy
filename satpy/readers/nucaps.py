#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (c) 2016.

# Author(s):

#
#   David Hoese <david.hoese@ssec.wisc.edu>
#

# This file is part of satpy.

# satpy is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.

# satpy is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE.  See the GNU General Public License for more details.

# You should have received a copy of the GNU General Public License along with
# satpy.  If not, see <http://www.gnu.org/licenses/>.

"""Interface to NUCAPS Retrieval NetCDF files

"""
import os.path
from datetime import datetime, timedelta
import numpy as np
import h5py
import logging
from collections import defaultdict

from satpy.readers import ConfigBasedReader, FileKey, GenericFileReader
from satpy.readers.netcdf_utils import NetCDF4FileWrapper
import six

NO_DATE = datetime(1958, 1, 1)
EPSILON_TIME = timedelta(days=2)
LOG = logging.getLogger(__name__)


ALL_PRESSURE_LEVELS = [
    0.0161, 0.0384, 0.0769, 0.137, 0.2244, 0.3454, 0.5064, 0.714,
    0.9753, 1.2972, 1.6872, 2.1526, 2.7009, 3.3398, 4.077, 4.9204,
    5.8776, 6.9567, 8.1655, 9.5119, 11.0038, 12.6492, 14.4559, 16.4318,
    18.5847, 20.9224, 23.4526, 26.1829, 29.121, 32.2744, 35.6505,
    39.2566, 43.1001, 47.1882, 51.5278, 56.126, 60.9895, 66.1253,
    71.5398, 77.2396, 83.231, 89.5204, 96.1138, 103.017, 110.237,
    117.777, 125.646, 133.846, 142.385, 151.266, 160.496, 170.078,
    180.018, 190.32, 200.989, 212.028, 223.441, 235.234, 247.408,
    259.969, 272.919, 286.262, 300, 314.137, 328.675, 343.618, 358.966,
    374.724, 390.893, 407.474, 424.47, 441.882, 459.712, 477.961,
    496.63, 515.72, 535.232, 555.167, 575.525, 596.306, 617.511, 639.14,
    661.192, 683.667, 706.565, 729.886, 753.628, 777.79, 802.371,
    827.371, 852.788, 878.62, 904.866, 931.524, 958.591, 986.067,
    1013.95, 1042.23, 1070.92, 1100
]


class NUCAPSFileReader(GenericFileReader):
    """NUCAPS File Reader
    """
    def create_file_handle(self, filename, **kwargs):
        handle = NetCDF4FileWrapper(filename, **kwargs)
        return handle.filename, handle

    def variable_path(self, item):
        if item.endswith("/shape") and item[:-6] in self.file_keys:
            item = self.file_keys[item[:-6]].variable_name.format(**self.file_info) + "/shape"
        elif item in self.file_keys:
            item = self.file_keys[item].variable_name.format(**self.file_info)
        return item

    def __getitem__(self, item):
        var_path = self.variable_path(item)
        data = self.file_handle[var_path]
        if item in self.file_keys:
            var_info = self.file_keys[item]
            if "index" in var_info.kwargs:
                data = data[int(var_info.kwargs["index"])]
            if "pressure_index" in var_info.kwargs:
                data = data[..., int(var_info.kwargs["pressure_index"])]

        return data

    def __contains__(self, item):
        return item in self.file_handle

    def _parse_l1b_datetime(self, datestr):
        return datetime.strptime(datestr, "%Y-%m-%dT%H:%M:%S.%fZ")

    @property
    def ring_lonlats(self):
        return self["gring_longitude"], self["gring_latitude"]

    def _get_start_time(self):
        return self._parse_l1b_datetime(self['coverage_start'])

    def _get_end_time(self):
        return self._parse_l1b_datetime(self['coverage_end'])

    @property
    def begin_orbit_number(self):
        return int(self['beginning_orbit_number'])

    @property
    def end_orbit_number(self):
        return int(self['ending_orbit_number'])

    @property
    def platform_name(self):
        # FIXME: If an attribute is added to the file, for now hardcode
        # res = self['platform_short_name']
        res = "NPP"
        if isinstance(res, np.ndarray):
            return str(res.astype(str))
        else:
            return res

    @property
    def sensor_name(self):
        res = self['instrument_short_name']
        if isinstance(res, np.ndarray):
            return str(res.astype(str))
        else:
            return res

    @property
    def geofilename(self):
        res = self['geo_file_reference']
        if isinstance(res, np.ndarray):
            return str(res.astype(str))
        else:
            return res

    def get_file_units(self, item):
        # What units should we expect from the file
        file_units = self.file_keys[item].file_units

        # Guess the file units if we need to (normally we would get this from the file)
        if file_units is None:
            # see if there is an attribute
            try:
                var_path = self.file_keys[item].variable_name
                file_units = self[var_path + "/attr/units"]
                # some file units don't follow the satpy standards
                if file_units == "none":
                    file_units = "1"
                return file_units
            except (AttributeError, KeyError):
                LOG.debug("No units attribute found for '%s'", item)

            if "radiance" in item:
                # we are getting some sort of radiance, probably DNB
                file_units = "W cm-2 sr-1"
            elif "reflectance" in item:
                # CF compliant unit for dimensionless
                file_units = "1"
            elif "temperature" in item:
                file_units = "K"
            elif "longitude" in item or "latitude" in item:
                file_units = "degrees"
            else:
                LOG.debug("Unknown units for file key '%s'", item)

        return file_units

    def get_shape(self, item):
        var_info = self.file_keys[item]
        shape = self[item + "/shape"]
        if "index" in var_info.kwargs:
            shape = shape[1:]
        if "pressure_index" in var_info.kwargs:
            shape = shape[:-1]
        return shape

    def adjust_scaling_factors(self, factors, file_units, output_units):
        if file_units == output_units:
            LOG.debug("File units and output units are the same (%s)", file_units)
            return factors
        if factors is None:
            factors = [1, 0]
        factors = np.array(factors)

        if file_units == "W cm-2 sr-1" and output_units == "W m-2 sr-1":
            LOG.debug("Adjusting scaling factors to convert '%s' to '%s'", file_units, output_units)
            factors[::2] = np.where(factors[::2] != -999, factors[::2] * 10000.0, -999)
            factors[1::2] = np.where(factors[1::2] != -999, factors[1::2] * 10000.0, -999)
            return factors
        elif file_units == "1" and output_units == "%":
            LOG.debug("Adjusting scaling factors to convert '%s' to '%s'", file_units, output_units)
            factors[::2] = np.where(factors[::2] != -999, factors[::2] * 100.0, -999)
            factors[1::2] = np.where(factors[1::2] != -999, factors[1::2] * 100.0, -999)
            return factors
        else:
            return factors

    def get_swath_data(self, item, data_out=None, mask_out=None):
        """Get swath data, apply proper scalings, and apply proper masks.
        """
        # Can't guarantee proper file info until we get the data first
        var_info = self.file_keys[item]
        var_path = self.variable_path(item)
        # NetCDF4 files for L1B have proper attributes so the NetCDF4 library
        # can auto scale and auto mask the data
        data = self[item]
        valid_max_path = var_path + "/attr/valid_max"
        valid_range_path = var_path + "/attr/valid_range"
        fill_value_path = var_path + "/attr/_FillValue"
        if valid_max_path in self:
            valid_max = self[valid_max_path]
        elif valid_range_path in self:
            valid_max = self[valid_range_path][-1]
        else:
            valid_max = None
        fill_value = self[fill_value_path] if fill_value_path in self else None
        data = data[:]

        if data_out is not None:
            # This assumes that we are promoting the dtypes (ex. float file data -> int array)
            # and that it happens automatically when assigning to the existing out array
            data_out[:] = data
        else:
            data_out = data[:].astype(var_info.dtype)
            mask_out = np.zeros_like(data_out, dtype=np.bool)

        # Check if we need to do some unit conversion
        # file_units = self.get_file_units(item)
        # output_units = getattr(var_info, "units", file_units
        if valid_max is not None:
            mask_out[:] |= data_out > valid_max
        if fill_value is not None:
            mask_out[:] |= data_out == fill_value

        if "lut" in var_info.kwargs:
            factors = None
            lut = self[var_info.kwargs["lut"]][:]
            # Note: Need to use the original data as `data_out` might be a non-integer data type
            data_out[:] = lut[data.ravel()].reshape(data.shape)
        elif var_info.scaling_factors:
            # L1B has 2 separate factors
            factors_name, offset_name = var_info.scaling_factors.split(",")
            try:
                factors = (self[factors_name], self[offset_name])
            except KeyError:
                LOG.debug("No scaling factors found for %s", item)
                factors = None
        else:
            factors = None

        # Check if we need to do some unit conversion
        file_units = self.get_file_units(item)
        output_units = getattr(var_info, "units", file_units)
        factors = self.adjust_scaling_factors(factors, file_units, output_units)

        if factors is not None:
            data_out *= factors[0]
            data_out += factors[1]

        return data_out, mask_out


class NUCAPSReader(ConfigBasedReader):
    """Reader for NUCAPS NetCDF4 files.
    """
    def __init__(self, default_file_reader=NUCAPSFileReader, default_config_filename="readers/nucaps.cfg", **kwargs):
        self.pressure_dataset_names = defaultdict(list)
        super(NUCAPSReader, self).__init__(default_file_reader=default_file_reader,
                                           default_config_filename=default_config_filename,
                                           **kwargs
                                           )

    def load_section_file_key(self, section_name, section_options):
        super(NUCAPSReader, self).load_section_file_key(section_name, section_options.copy())
        if "pressure_based" in section_options:
            # FUTURE: Pass pressure index from the dataset object to the file readers
            for idx, lvl_num in enumerate(ALL_PRESSURE_LEVELS):
                if lvl_num < 5.0:
                    suffix = "_{:0.03f}mb".format(lvl_num)
                else:
                    suffix = "_{:0.0f}mb".format(lvl_num)
                new_section_options = section_options.copy()
                new_section_name = section_name + suffix
                new_section_options["pressure_level"] = lvl_num
                new_section_options["pressure_index"] = idx
                super(NUCAPSReader, self).load_section_file_key(new_section_name, new_section_options)

    def load_section_dataset(self, section_name, section_options):
        super(NUCAPSReader, self).load_section_dataset(section_name, section_options.copy())
        if "pressure_based" in section_options:
            base_name = section_options["name"]
            for idx, lvl_num in enumerate(ALL_PRESSURE_LEVELS):
                if lvl_num < 5.0:
                    suffix = "_{:0.03f}mb".format(lvl_num)
                else:
                    suffix = "_{:0.0f}mb".format(lvl_num)
                new_section_options = section_options.copy()
                new_section_options["name"] = base_name + suffix
                new_section_options["file_key"] = section_options["file_key"] + suffix
                new_section_options["pressure_level"] = lvl_num
                new_section_options["pressure_index"] = idx
                self.pressure_dataset_names[base_name].append(base_name + suffix)
                super(NUCAPSReader, self).load_section_dataset(section_name, new_section_options)

    def load(self, datasets_to_load, metadata=None, pressure_levels=None, **dataset_info):
        if pressure_levels is not None and "pressure_levels" not in metadata:
            LOG.debug("Adding 'pressure_levels' to metadata for pressure level filtering")
            metadata.add("pressure_levels")

        if pressure_levels is not None:
            # Filter out datasets that don't fit in the correct pressure level
            for ds_id in datasets_to_load[:]:
                ds_info = self.datasets[ds_id]
                ds_level = ds_info.get("pressure_level")
                if ds_level is not None:
                    if pressure_levels is True:
                        # they want all pressure levels
                        True
                    elif len(pressure_levels) == 2 and pressure_levels[0] <= ds_level <= pressure_levels[1]:
                        # given a min and a max pressure level
                        continue
                    elif np.isclose(pressure_levels, ds_level).any():
                        # they asked for this specific pressure level
                        continue
                    else:
                        # they don't want this dataset at this pressure level
                        datasets_to_load.remove(ds_id)
                        continue

        datasets_loaded = super(NUCAPSReader, self).load(datasets_to_load, metadata=metadata, **dataset_info)

        if pressure_levels is not None:
            for ds_id in datasets_loaded.keys():
                ds_obj = datasets_loaded[ds_id]
                ds_levels = ds_obj.info.get("pressure_levels")
                if ds_levels is None:
                    LOG.debug("No 'pressure_levels' metadata included in dataset")
                    continue
                if ds_levels.shape[0] != ds_obj.shape[-1]:
                    LOG.debug("Dataset '{}' doesn't contain multiple pressure levels".format(ds_id))
                    continue

                if pressure_levels is True:
                    levels_mask = np.ones(ds_levels.shape, dtype=np.bool)
                elif len(pressure_levels) == 2:
                    # given a min and a max pressure level
                    levels_mask = (ds_levels <= pressure_levels[1]) & (ds_levels >= pressure_levels[0])
                else:
                    levels_mask = np.zeros(ds_levels.shape, dtype=np.bool)
                    for idx, ds_level in enumerate(ds_levels):
                        levels_mask[idx] = np.isclose(pressure_levels, ds_level).any()

                datasets_loaded[ds_id] = ds_obj[:, levels_mask]
                datasets_loaded[ds_id].info["pressure_levels"] = ds_levels[levels_mask]

        return datasets_loaded


