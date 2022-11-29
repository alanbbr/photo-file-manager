#!/usr/bin/python3
# pylint: disable=protected-access,unused-import
"""
Unit tests for photo-file-manager.py
"""

import datetime
import logging
import os
import pathlib
import time
import unittest
import pytz
from photofileman import convert_to_decimal, logger, PhotoFileMan

class TestConvertToDecimal(unittest.TestCase):
    """
    Test the non-class function.
    Use integer comparison because float comparison can be hardware specific.
    """
    def test_north(self):
        """Test north latitude. Expect a positive number."""
        lat = int(convert_to_decimal("""59 deg 54' 4" N""") * 10_000)
        self.assertEqual(lat, 599011)

    def test_east(self):
        """Test east longitude. Expect a positive number."""
        lon = int(convert_to_decimal("""10 deg 44' 20" E""") * 10_000)
        self.assertEqual(lon, 107388)

    def test_south(self):
        """Test south latitude. Expect a negative number."""
        lat = int(convert_to_decimal("""34 deg 53' 1" S""") * 10_000)
        self.assertEqual(lat, -348836)

    def test_west(self):
        """Test west longitude. Expect a negative number."""
        lon = int(convert_to_decimal("""56 deg 10' 55" W""") * 10_000)
        self.assertEqual(lon, -561819)

    def test_bad_input(self):
        """Test unexpected input format, expecting a ValueError."""
        self.assertRaises(ValueError, convert_to_decimal, "junk")

class TestData(unittest.TestCase):
    """Define a common set of command line options."""
    test_args = {"dry_run": True,
                 "scan_dirs": False,
                 "force": False,
                 "convert": False,
                 "rename": False,
                 "image_description": False,
                 "touch": False,
                 "month": False,
                 "geo_group": False,
                 "since": False,
                 "command": "copy", # ["copy", "move", "convert", "rename", "touch"],
                 "source": "/tmp",
                 "destination": "/var/tmp"}
    pfm = PhotoFileMan(test_args)
    naive = datetime.datetime(1999, 12, 31, 23, 59, 59)
    naivems = datetime.datetime(1999, 12, 31, 23, 59, 59, 2000)
    utc = datetime.datetime(1999, 12, 31, 23, 59, 59, 2000, pytz.utc)
    nyc = pytz.timezone("US/Eastern").localize(naive)
    nycms = pytz.timezone("US/Eastern").localize(naivems)
    sng = pytz.timezone("Asia/Singapore").localize(naive)
    sngms = pytz.timezone("Asia/Singapore").localize(naivems)

class TestPhotoFileManParseDate(TestData):
    """Verify date only format parsing."""
    def test_good_dates(self):
        """Don't test %x format, as that will vary."""
        expect = datetime.date(1999, 12, 31)
        self.assertEqual(self.pfm._parse_date('1999-12-31'), expect)
        self.assertEqual(self.pfm._parse_date('12/31/99'), expect)
        self.assertEqual(self.pfm._parse_date('1999:12:31'), expect)

    def test_bad_dates(self):
        """Send unparseables and see what happens."""
        self.assertEqual(self.pfm._parse_date('junk'), None)

class TestPhotoFileManGetCacheFile(TestData):
    """
    This is trivial now, but it would nice to handle Mac OS and Windows paths.
    """
    def test_get_cache_file(self):
        """Linux only"""
        val = pathlib.Path(os.environ['HOME']).joinpath('.cache').joinpath('photofileman_geodata.pickle')
        self.assertEqual(self.pfm._get_cache_file(), val)

class TestPhotoFileManParseTimestamp(TestData):
    """Verify datetime with and without timezone information format parsing."""
    def test_same_date(self):
        """If sent a datetime, _parse_timestamp should just return it."""
        self.assertEqual(self.pfm._parse_timestamp(self.naive), self.naive)
        self.assertEqual(self.pfm._parse_timestamp(self.utc), self.utc)

    def test_to_the_second_datetimes(self):
        """Second resolution only data."""
        self.assertEqual(self.pfm._parse_timestamp("1999:12:31 23:59:59-05:00"), self.nyc)
        self.assertEqual(self.pfm._parse_timestamp("1999:12:31 23:59:59+08:00"), self.sng)

    def test_millisecond_datetimes(self):
        """Milliseconds is what seems to be most common in EXIF."""
        self.assertEqual(self.pfm._parse_timestamp("1999:12:31 23:59:59.002-05:00"), self.nycms)
        self.assertEqual(self.pfm._parse_timestamp("1999:12:31 23:59:59.002+08:00"), self.sngms)

    def test_iso_format_datetimes(self):
        """I don't know why EXIF seems to like colon separated dates, not the standard."""
        self.assertEqual(self.pfm._parse_timestamp("1999-12-31T23:59:59.002-05:00"), self.nycms)
        self.assertEqual(self.pfm._parse_timestamp("1999-12-31 23:59:59.002+08:00"), self.sngms)

    def test_text_timezone_datetimes(self):
        """These don't seem to happen often."""
        self.assertEqual(self.pfm._parse_timestamp("1999:12:31 23:59:59EST"), self.nyc)

    def test_local_time(self):
        """Kind of silly, since it's basically doing the same thing."""
        tz = time.strftime("%Z", time.localtime())
        local_time = pytz.timezone(tz).localize(self.naive)
        self.assertEqual(self.pfm._parse_timestamp("1999:12:31 23:59:59"), local_time)

class TestPhotoFileManCheckSource(TestData):
    """Verify date based skipping."""
    def test_check_source(self):
        """Verify skip and process dates."""
        self.pfm.metadata['first_date'] = self.nyc # "photo" date
        self.pfm.since = None
        self.assertFalse(self.pfm.check_source()) # nothing means continue
        self.pfm.since = self.nyc.date()
        self.assertTrue(self.pfm.check_source())  # cut-off same as photo date means skip
        self.pfm.since = (self.nyc - datetime.timedelta(days=1)).date()
        self.assertFalse(self.pfm.check_source()) # cut-off one day before photo date mean process the photo
        self.pfm.since = (self.nyc + datetime.timedelta(days=1)).date()
        self.assertTrue(self.pfm.check_source())  # cut-off one day after photo date means skip

if __name__ == '__main__':
    unittest.main()
