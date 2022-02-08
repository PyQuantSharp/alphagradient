# -*- coding: utf-8 -*-
"""AG module containing tools for creation/manipulation of asset data

This module contains tools for accessing, creating, and modifying data and datasets for use in AlphaGradient objects.

Todo:
    * Implement dtype coercion on column validation
    * Type Hints
"""

# Standard imports
from collections import namedtuple
from datetime import datetime
from copy import deepcopy
# from typing import List
from os import scandir
import pickle
import weakref

# Third party imports
from aenum import Enum, NoAlias, auto, extend_enum, skip, unique
import pandas as pd
import numpy as np

# Local imports
from ..constants import is_numeric

def get_data(asset):
    """Accesses locally stored data relevant to this asset

    Args:
        asset (Asset): The asset to retrieve data for

    Returns:
        data (AssetData): Stored dataset relevant to the asset
    """

    key = asset.key
    data = None

    # Getting the data from pickles
    path = f"AlphaGradient/data/pickles/{key}.p"
    try:
        data = pd.read_pickle(path)
        return AssetData(asset.__class__, data)
    except FileNotFoundError:
        pass

    # Getting the data from raw files
    path = f"AlphaGradient/data/raw/{key}.csv"
    try:
        return AssetData(asset.__class__, path)
    except FileNotFoundError:
        return None


class AssetData:
    """Datetime-indexed datesets that store financial data for assets.

    All AlphaGradient assets seeking to use tabular data must use AssetData datasets. AssetData accepts any of the following inputs:
        * numbers (for assets with constant prices, or unit prices)
        * os.path-like objects
        * file-object-like objects
        * array-like objects (lists, ndarray, etc.)
        * pathstrings
        * pandas DataFrames

    AssetDatasets take tabular data and clean/validate it to make it usable for ag assets. They check for the presence of required data, remove unnecessary data, ensure that dataframes are datetime-indexed, coerce data dtypes (TODO), and ensure that formatting is consistent between all assets.

    Attributes:
        data (pd.DataFrame): The dataset for the asset
        open_value (str): The column to associate with open
        close_value (str): The column to associate with close
        last (datetime): The last available date for this dataset
    """

    def __init__(self, asset_type, data, columns=None):
        # Unpacking necessary values from the asset type
        _, _, _, required, optional, close_value, open_value = asset_type.get_settings(unpack=True)

        # Formatting required columns
        required = required if required else []
        required = [self.column_format(column) for column in required]

        # null case
        if data is None:
            return None

        # Handle numeric inputs that default to static dataframes
        if is_numeric(data):
            frame = [[datetime.today()] + ([data] * (len(required) + 1))]
            close_value = close_value if close_value else "CLOSE"
            required = ["DATE", close_value] + required
            data = pd.DataFrame(frame, columns=required)

        # Handle list inputs, np.ndarray inputs
        if isinstance(data, (list, np.ndarray)):
            if not columns:
                raise ValueError(f"{type(data).__name__} input requires explicit column names during initialization")
            data = pd.DataFrame(data, columns=columns)

        # Handle inputs that can be processed by pd.read_table
        try:
            data = pd.read_table(data, sep=',')
        except (TypeError, ValueError) as e:
            pass

        # Final check that we have valid data prior to formatting
        if isinstance(data, pd.DataFrame):
            if isinstance(data.index, pd.core.indexes.datetimes.DatetimeIndex):
                data.index.name = "DATE"
                data["DATE"] = data.index
        else:
            raise ValueError(f"Unable to create valid asset dataset from {data}")

        # Formatting columns
        data.columns = [self.column_format(column) for column in data.columns]

        # Grabbing "OPEN" and "CLOSE" by defauly if not specified
        open_value = "OPEN" if ("OPEN" in data.columns and not open_value) else open_value
        close_value = "CLOSE" if ("CLOSE" in data.columns and not close_value) else close_value

        # Broadcasting open to close or close to open in case only one is provided
        if close_value and not open_value:
            close_value = self.column_format(close_value)
            open_value = close_value
            self.single_valued = True

        elif open_value and not close_value:
            open_value = self.column_format(open_value)
            close_value = open_value
            self.single_valued = True

        # By this point both should be present if even one was provided
        elif not all([close_value, open_value]):
            raise ValueError(f"Must specify at least one opening or closing value name present in the data")

        # Both an open and close have been explicitly provided
        else:
            open_value = self.column_format(open_value)
            close_value = self.column_format(close_value)
            self.single_valued = False

        # Attribute initialization
        self.open_value = open_value
        self.close_value = close_value

        # Adding default required columns (open, close, date)
        if close_value:
            required = [close_value] + required

        if open_value:
            required = [open_value] + required

        required = ["DATE"] + required

        # Removing duplicates
        required = list(set(required))

        # Both of the values (open and close) must be in required
        if not all([value in required for value in [open_value, close_value]]):
            raise ValueError(f"Must specify at least one opening or closing value name present in the data")

        # Final formatting requirements
        data = self.validate_columns(data, required, optional)

        # Setting date column as DatetimeIndex
        data = data.set_index(pd.DatetimeIndex(data["DATE"]))
        data.drop("DATE", axis=1, inplace=True)

        self.data = data

    def __getattr__(self, attr):
        try:
            return self.data.__getattr__(attr)
        except AttributeError:
            try:
                return self.data[attr]
            except KeyError as e:
                raise AttributeError(f"\'AssetData\' object has no attribute {e}")

    def __getitem__(self, item):
        return self.data[item]

    def __str__(self):
        return self.data.__str__()

    def __bool__(self):
        return not self.data.empty

    @staticmethod
    def column_format(column):
        """The standard string format for columns

        Takes a column name string and returns it in uppercase, with spaces replaced with underscores

        Args:
            column (str): The column name to be altered

        Returns:
            column (str): The altered column name
        """
        return column.replace(' ', '_').upper()

    def validate_columns(self, data, required, optional):
        """Ensures that the input data meets the formatting requirements to be a valid asset dataset

        To be a valid asset dataset, input tabular data must have every column listed in 'required'. This method ensures that all of the required columns are present, as well as removes columns that don't show up in either the required or optional lists.

        TODO: 
            * allow dictionaries to be passed in to enforce specific column dtypes.
            * Implement more checks to ensure that data is error-free, and lacks missing elements (or implement measures to be safe in the presence of missing data)

        Args:
            data (tabular data): the data being validated
            required (list of str): a list of strings representing
                column names. All of the columns in this list must be present to produce a viable dataset.
            optional (list of str): a list of strings representing
                column names. Columns in the data that are not required will still be kept in the data if they are present in this list. Otherwise, they will not be included.

        Returns:
            data (tabular data): a verified (and slightly modified)
                asset dataset

        Raises:
            ValueError: raised when the input dataset does not satisfy
                the requirements
        """
        # Check for column requirements
        required = required if required else {}
        optional = optional if optional else {}

        # Converting required and optional to dicts
        def to_dict(columns):
            if isinstance(columns, list):
                columns = [self.column_format(column) for column in columns]
                columns = {column: 'float' for column in columns}

            elif isinstance(columns, dict):
                columns = {self.column_format(column): dtype for column, dtype in columns.items()}

            return columns

        required = to_dict(required)
        optional = to_dict(optional)

        # Checking whether all of the required columns have been satisfied
        satisfied = {column : (column in data.columns) for column in required}
        unsatisfied = [column for column, present in satisfied.items() if not present]
        if unsatisfied:
            unsatisfied = str(unsatisfied)[1:-1]
            raise ValueError(f"AssetData missing required columns: {unsatisfied}")

        # Coercing dtypes to those specified in required and optional
        # CURRENTLY NOT IMPLENENTED, REQUIRED PASSED IN AS LIST
        '''
        for column_dict in [required, optional]:
            for column, dtype in column_dict:
                self.data[column] = self.data[column].astype(dtype)
        '''

        # Dropping columns that are not present in optional or required
        for column in data.columns:
            if column not in list(required) + list(optional):
                data.drop(column, axis=1, inplace=True)

        return data



# NOTE: THIS IS ONLY RELEVANT FOR COLUMN ENUM DATASETS.
# THE NEW IMPLEMENTATION DOES NOT REQUIRE THIS.

'''
This is a little bit hacky, but this this needs to be defined outside of the scope of AssetData even though it is only intended to be used in that class. This is because the COLUMNS enum defined within will not allow the use of subclasses as values for enum members. By defining it outside, we can use Value within the COLUMNS enum scope, allowing us to bypass the requirement that all values be the same. Ordinarily, we could just use 'settings=NoAlias', but it imposes too many restrictions when loading saved asset datasets from pickles.
'''

#Value = namedtuple('Value', 'type name')




# Below is the implementation of asset datasets that use enumerations for columns names. This may be revisited in the future
'''
class AssetData(pd.DataFrame):

    class COLUMNS(Enum):

        DATE = Value('datetime64', 'DATE')
        OPEN = Value('float', 'OPEN')
        CLOSE = Value('float', 'CLOSE')

        def __str__(self):
            return self.name

        def __repr__(self):
            return f'\'{self.name}\''

        @property
        def type(self):
            return self.value[0]

    static = False

    def __init__(self, data=None, required=None, optional=None):

        # Check for column requirements
        required = required if required else {}
        optional = optional if optional else {}

        def column_format(column):
            return column.replace(' ', '_').upper()

        # Converting required and optional to dicts
        def to_dict(columns):
            if isinstance(columns, list):
                columns = [column_format(column) for column in columns]
                columns = {column: Value('float', column)
                           for column in columns}

            elif isinstance(columns, dict):
                columns = {
                    column_format(column): Value(
                        columns[column],
                        column_format(column)) for column in columns}

            return columns

        required = to_dict(required)
        optional = to_dict(optional)

        # Update COLUMNS enum to accomodate requirements
        columns = [self.DATE, self.OPEN, self.CLOSE]

        def extend_columns(columns):
            enums = [column.name for column in self.COLUMNS]
            columns = {
                k.replace(
                    ' ',
                    '_').upper(): v for k,
                v in columns.items()}
            for name in columns:
                if name not in enums:
                    extend_enum(self.COLUMNS, name, columns[name])

        extend_columns(required)
        extend_columns(optional)

        # Updating columns to contain requirements
        columns += [self.COLUMNS[name] for name in required]

        # Handling AssetData inputs
        if isinstance(data, AssetData):
            if all([column in data.columns for column in required]):
                super().__init__(data)
                return
            else:
                raise ValueError('Dataset missing required columns')

        # Handling NoneType inputs
        data = 0 if data is None else data

        # Handling inputs that will result in single row dataframes
        if is_numeric(data):
            self.static = True
            data = pd.DataFrame(
                [[datetime.today()] + [data] * (len(columns) - 1)], columns=columns, dtype=float)

        # Handling non DataFrame inputs, checking for required columns
        else:
            if isinstance(data, str):
                data = pd.read_csv(data)
            elif isinstance(data, np.ndarray):
                data = pd.DataFrame(data)

            # Converting all columns to enums, dropping all columns which do
            # not exist in the enumeration
            to_convert = []
            to_drop = []
            data.columns = [column.replace(' ', '_').upper()
                            for column in data.columns]
            available = [column.name for column in self.COLUMNS]
            for column in data.columns:
                if column in available:
                    to_convert.append(self.COLUMNS[column])
                else:
                    to_drop.append(column)
            data.drop(to_drop, axis=1, inplace=True)
            data.columns = to_convert

            # Verifying existence of all required columns
            if not all([column in data.columns for column in columns]):
                raise ValueError(f'Dataset missing required columns')

            # Verifying column-specific dtypes
            data = data.astype(
                {column: column.type for column in data.columns})

        # Making the dataset datetime indexed, removing duplicate column
        if self.static or not data.empty:
            data = data.set_index(pd.DatetimeIndex(data[self.DATE]))
            data.drop(self.DATE, axis=1, inplace=True)

        super().__init__(data=data)

    # The limited scope of asset datasets allows the explicit definition of
    # boolean conversions
    def __bool__(self):
        return not (self.empty or self.static)

    # Allows the user to get COLUMN enum members as though they were
    # attributes of the dataset
    def __getattr__(self, attr):
        try:
            return self.COLUMNS[attr]
        except KeyError:
            return super().__getattr__(attr)

    # Allows the user to use strings to dynamically access the enum column
    # names
    def __getitem__(self, item):
        try:
            item = self.COLUMNS[item]
        except KeyError:
            pass
        return super().__getitem__(item)

    # Allows the user to dynamically access column enum members
    def get_column(self, column):
        try:
            return self.COLUMNS[column.replace(' ', '_').upper()]
        except KeyError:
            return None
'''

# OLD LEDGER SYSTEM

'''

class Ledger(pd.DataFrame):

    def instance(): return None

    def __new__(cls, *args):
        if cls.instance() is not None:
            return cls.instance()
        return super().__new__(cls)

    def __init__(self, data=None):
        # Try to access a ledger if one already exists
        try:
            data = data if data is not None else pd.read_pickle(
                'AlphaGradient/data/ledger')

            if not isinstance(data, (Ledger, pd.DataFrame)):
                raise TypeError('Invalid input for Ledger')

            super().__init__(data)

        # Otherwise, create a new one
        except FileNotFoundError:
            data = pd.DataFrame(
                columns=[
                    'ID',
                    'TYPE',
                    'NAME',
                    'STATUS',
                    'DATE'])
            super().__init__(data)
            self.auto_update()

        Ledger.instance = lambda: self

    def append(self, *args):
        data = super().append(*args)
        return Ledger(data)

    def to_pickle(self):
        pd.to_pickle(self, 'AlphaGradient/data/ledger')

    def auto_update(self):

        for raw_file in scandir('AlphaGradient/data/raw'):
            name = raw_file.name.split('.')[0]
            data = self.loc[self['ID'] == name]
            if data.empty:
                asset_type, asset_name = self.id_info(name)
                entry = pd.DataFrame([[name, asset_type, asset_name, 1, datetime.today()]], columns=[
                                     'ID', 'TYPE', 'NAME', 'STATUS', 'DATE'])
                self = self.append(entry)
            else:
                index = data.index.item()
                self.at[index, 'STATUS'] = 1
                self.at[index, 'DATE'] = datetime.today()

        for pickle in scandir('AlphaGradient/data/pickles'):
            data = self.loc[self['ID'] == pickle.name]
            if data.empty:
                asset_type, asset_name = self.id_info(pickle.name)
                entry = pd.DataFrame([[pickle.name, asset_type, asset_name, 2, datetime.today(
                )]], columns=['ID', 'TYPE', 'NAME', 'STATUS', 'DATE'])
                self = self.append(entry)
            else:
                index = data.index.item()
                self.at[index, 'STATUS'] = 2
                self.at[index, 'DATE'] = datetime.today()

        self.to_pickle()

    def update(self, data):
        pass

    def update_entry(self):
        pass

    def add_entry(self):
        pass

    @staticmethod
    def id(asset_type, asset_name):
        return f'{asset_type}_{asset_name}'.strip().upper()

    @staticmethod
    def id_info(ledger_id):
        # Decompose the ID
        info = ledger_id.split('_', 1)

        # Split into relevant information
        asset_type = info[0]
        asset_name = info[1]

        return asset_type, asset_name

    def get_status(self, ledger_id, asset_name=None):
        if asset_name is not None:
            ledger_id = self.id(ledger_id, asset_name)

        entry = self.loc[self['ID'] == ledger_id]

        if not entry.empty:
            return entry['STATUS'].item()

        return 0


def get_data_ledger(asset_type, asset_name, ledger=None):
    if not isinstance(ledger, Ledger):
        ledger = Ledger()

    ledger_id = ledger.id(asset_type, asset_name)
    status = ledger.get_status(ledger_id)

    data = None

    if status > 1:
        data = from_pickle(asset_type, asset_name)
        status = status - 1 if data is None else status

    if status <= 1:
        data = from_raw(asset_type, asset_name)

    return data

def from_pickle_ledger(asset_type, asset_name, ledger=None):
    # Check if an acceptable ledger is passed in
    if not isinstance(ledger, Ledger):
        ledger = Ledger()

    # Accessing the entry for this asset
    ledger_id = ledger.id(asset_type, asset_name)
    status = ledger.get_status(ledger_id)

    if status > 1:
        try:
            return pd.read_pickle(f'AlphaGradient/data/pickles/{ledger_id}')

        except FileNotFoundError:
            if status == 2:
                print('update ledger!')
            return None

    return None


def from_raw_ledger(asset_type, asset_name, ledger=None):
    # Check if an acceptable ledger is passed in
    if not isinstance(ledger, Ledger):
        ledger = Ledger()

    # Accessing the entry for this asset
    ledger_id = ledger.id(asset_type, asset_name)
    status = ledger.get_status(ledger_id)

    if status > 0:
        try:
            return AssetData(
                pd.read_csv(f'AlphaGradient/data/raw/{ledger_id}'))

        except FileNotFoundError:
            if status == 1:
                print('update ledger!')
            return None

    return None
'''
                

