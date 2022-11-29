#!/usr/bin/python3
"""
:author: Alan Brenner <alan@abcompcons.com>
"""
import hashlib
import logging
import os
import pickle
import pprint
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

import piexif
import pytz
from cyheifloader import cyheif
from PIL import ExifTags, Image
from hachoir.parser import createParser
from hachoir.metadata import extractMetadata
from shapely.geometry import Point

try:
    from geopy.geocoders import Nominatim
except ImportError:
    Nominatim = None


BLOCKSIZE = 65536
# In order from most preferred to least:
TIME_KEYS = ("DateTime", "DateTimeOriginal", "DateTimeDigitized")
BUFFER = 0.05
EXIFTOOL2PIL = {
    # 'Acceleration Vector': ('Acceleration', 37892),
    'Aperture Value': ('ApertureValue', 37378),
    'Brightness Value': ('BrightnessValue', 37379),
    'Camera Model Name': ('Model', 272),
    'Color Space': ('ColorSpace', 40961),
    'Create Date': ('DateTime', 306),
    'Date/Time Original': ('DateTimeOriginal', 36867),
    'Exif Image Height': ('ExifImageHeight', 40963),
    'Exif Image Width': ('ExifImageWidth', 40962),
    'Exif Version': ('ExifVersion', 36864),
    'Exposure Mode': ('ExposureMode', 41986),
    'Exposure Program': ('ExposureProgram', 34850),
    'Exposure Time': ('ExposureTime', 33434),
    'F Number': ('FNumber', 33437),
    'File Modification Date/Time': ('DateTimeDigitized', 36868),
    'Flash': ('Flash', 37385),
    'Focal Length': ('FocalLength', 37386),
    'Focal Length In 35mm Format': ('FocalLengthIn35mmFilm', 41989),
    'GPS Altitude': ('GPSAltitude', 6),
    'GPS Altitude Ref': ('GPSAltitudeRef', 5),
    'GPS Date Stamp': ('DateTime', 29), # Override Create Date, as this seems to have time zone always (Z)
    'GPS Dest Bearing': ('GPSDestBearing', 24),
    'GPS Dest Bearing Ref': ('GPSDestBearingRef', 23),
    'GPS Horizontal Positioning Error': ('GPSHPositioningError', 31),
    'GPS Img Direction': ('GPSImgDirection', 17),
    'GPS Img Direction Ref': ('GPSImgDirectionRef', 16),
    'GPS Latitude': ('GPSLatitude', 2),
    'GPS Latitude Ref': ('GPSLatitudeRef', 1),
    'GPS Longitude': ('GPSLongitude', 4),
    'GPS Longitude Ref': ('GPSLongitudeRef', 3),
    'GPS Speed': ('GPSSpeed', 13),
    'GPS Speed Ref': ('GPSSpeedRef', 12),
    'Host Computer': ('HostComputer', 316),
    'ISO': ('ISOSpeed', 34867),
    'Image Height': ('ImageLength', 257),
    'Image Width': ('ImageWidth', 256),
    'Lens ID': ('LensSpecification', 42034),
    # 'Lens Info': ('LensInfo', 50736),
    'Lens Make': ('LensMake', 42035),
    'Lens Model': ('LensModel', 42036),
    'Make': ('Make', 271),
    'Offset Time': ('OffsetTime', 36880),
    'Offset Time Digitized': ('OffsetTimeDigitized', 36882),
    'Offset Time Original': ('OffsetTimeOriginal', 36881),
    'Orientation': ('Orientation', 274),
    'Profile Copyright': ('ProfileCopyright', 50942),
    'Profile Description': ('ProfileName', 50936),
    'Resolution Unit': ('ResolutionUnit', 296),
    'Scene Type': ('SceneType', 41729),
    # 'Sensing Method': ('SensingMethod', 41495),
    'Shutter Speed Value': ('ShutterSpeedValue', 37377),
    'Software': ('Software', 305),
    'Sub Sec Time Digitized': ('SubsecTimeDigitized', 37522),
    'Sub Sec Time Original': ('SubsecTimeOriginal', 37521),
    'Warning': ('UserComment', 37510),
    'X Resolution': ('XResolution', 282),
    'Y Resolution': ('YResolution', 283)}
EXIF_INTS = (('ImageWidth', 256),
             ('ImageLength', 257),
             ('XResolution', 282),
             ('YResolution', 283))
PIEXIF_MAP = {"0th": "ImageIFD",
              "Exif": "ExifIFD",
              "GPS": "GPSIFD",
              "Interop": "InteropIFD"}
EXIF_TAGS = {vv: kk for kk, vv in ExifTags.TAGS.items()}

logging.basicConfig(
    format="%(asctime)-15s %(levelname)s:%(name)s:%(funcName)s:%(lineno)d:%(message)s"
)
logger = logging.getLogger("photofileman")


def convert_to_decimal(data) -> float:
    """
    Convert something like "40 deg 13' 6.96" N" to 40.2186 while handling N/S, E/W.

    :return: single floating point version of given location
    :raise: ValueError on parsing problems
    """
    logger.debug("parsing string: %s", data)
    parts = data.split(" ")
    try:
        pm = 1.0 if parts[-1] in ("N", "E") else -1.0
        minutes = float(parts[2][0:-1])
        seconds = float(parts[3][0:-1]) / 60.0
        return pm * (float(parts[0]) + (minutes + seconds) / 60)
    except (IndexError, ValueError):
        raise ValueError(f"Cannot parse {data}")  # pylint: disable=raise-missing-from


class PhotoFileMan:
    """
    Manipulate images, particularly as available from an iOS device.
    """

    def __init__(self, args):
        self.args = args
        logger.debug(pprint.pformat(self.args))
        self.since = self._parse_date(self.args['since'])
        self.metadata = {}
        self.exiftool = {}
        self.geodata = {}
        self.nominatim = None
        if Nominatim is not None and self.args["geo_group"]:
            self.nominatim = Nominatim(user_agent="photofileman")
            self.cache_geodata()

    def _parse_date(self, date_input):
        """
        Convert the optional since parameter to a datetime that can be used to filter images.
        """
        if not date_input:
            return None
        for ii in ('%Y-%m-%d', '%x', '%m/%d/%y', '%Y:%m:%d'):
            try:
                return datetime.strptime(date_input, ii).date()
            except ValueError:
                pass
        return None

    def _get_cache_file(self) -> Path:
        """
        Calculate the name of a cache file to use for geodata.
        """
        rval = Path(os.environ['HOME']).joinpath('.cache').joinpath('photofileman_geodata.pickle')
        logger.debug(rval)
        return rval

    def cache_geodata(self):
        """
        Walk the output directory, getting any existing name/location pairs.
        """
        cache = Path(self._get_cache_file())
        if cache.exists():
            with open(cache, 'rb') as input_file:
                self.geodata = pickle.load(input_file)
            for kk, vv in self.geodata.items():
                if not isinstance(vv, list):
                    self.geodata[kk] = [vv, ]
        if not self.args['scan_dirs']:
            return
        key = None
        logger.debug("Scanning %s for locations", self.args["destination"])
        for ff in list(Path(self.args["destination"]).rglob("*")):
            if ff.is_dir():
                logger.debug("directory: %s", ff.as_posix())
                if ff.stem == '.comments':
                    continue
                try:
                    int(ff.name)
                    continue
                except ValueError:
                    logger.debug("found non-number %s", ff.name)
                key = (ff.as_posix(), ff.name)
                if key[1] not in self.geodata:
                    logger.debug("saving %s", key[1])
                    self.geodata[key[1]] = None
            if ff.is_file() and key is not None:
                if ff.suffix.lower() == '.xml':
                    continue
                logger.debug("file: %s", ff.as_posix())
                if not ff.as_posix().startswith(key[0]):
                    logger.debug("erasing key %s", key[1])
                    key = None
                    continue
                metadata = self._get_exif(ff)
                if metadata and 'Longitude' in metadata and 'Latitude' in metadata:
                    logger.debug("found %r, %r", metadata['Longitude'], metadata['Latitude'])
                    if self.geodata[key[1]] is None:
                        point = Point(metadata['Longitude'], metadata['Latitude'])
                        buffer = point.buffer(BUFFER)
                        self.geodata[key] = [(point, buffer), ]
                        continue
                    if not self.geodata[key].contains(Point(metadata['Longitude'], metadata['Latitude'])):
                        logger.warning("%s is not near expected location %s", ff, key[1])
        for kk in list(self.geodata):
            if self.geodata[kk] is None:
                del self.geodata[kk]
        logger.debug(pprint.pformat(self.geodata))

    def save_geodata(self):
        """
        Save geodata into a local cache file that can be read by cache_geodata.
        """
        with open(self._get_cache_file(), 'wb') as outfile:
            pickle.dump(self.geodata, outfile)

    def get_geoname(self):
        """
        Get EXIF location data from cache or querying a service.
        """
        logger.debug("lon = %r, lat = %r, cache = %d", self.metadata['Longitude'], self.metadata['Latitude'], len(self.geodata))
        point = Point(self.metadata['Longitude'], self.metadata['Latitude'])
        for kk, vv in self.geodata.items():
            logger.debug("%s, %r", kk, vv[0].coords.xy)
            if vv[1].contains(point):
                self.metadata['place'] = kk
                return
        if not self.nominatim:
            return
        logger.debug("querying OpenStreetMap")
        addr = self.nominatim.reverse(f"{self.metadata['Latitude']}, {self.metadata['Longitude']}")
        logger.debug(pprint.pformat(addr.raw))
        time.sleep(2)
        key = []
        if 'ISO3166-2-lvl4' in addr.raw['address']:
            key.append(addr.raw['address']['ISO3166-2-lvl4'])
        else:
            if 'state' in addr.raw['address']:
                key.append(addr.raw['address']['state'].replace(' ', '_'))
            if 'country' in addr.raw['address']:
                key.append(addr.raw['address']['country'].replace(' ', '_'))
            elif 'country_code' in addr.raw['address']:
                key.append(addr.raw['address']['country_code'])
        for kk in ('village', 'town', 'city', 'county'):
            vv = addr.raw['address'].get(kk, '')
            if vv:
                key.append(vv.replace(' ', '_'))
                break
        self.metadata['place'] = '-'.join(key)
        if not self.metadata['place']:
            logger.error("Cannot find a geography name from %r", addr.raw)
            return
        if self.metadata['place'] not in self.geodata:
            buffer = point.buffer(BUFFER)
            self.geodata[self.metadata['place']] = [point, buffer]
            logger.debug("adding %s: %r to cache", self.metadata['place'], point.coords.xy)
        else:
            logger.warning("%s exists in the cache, but the location doesn't match. %r != %r -> %f",
                            self.metadata['place'], self.geodata[key][0].coords.xy,
                            point.coords.xy, point.distance(self.geodata[key][0]))

    def _exiftool(self, filepath: Path) -> dict:
        """
        Get date from video file.
        """
        logger.debug(filepath)
        output = subprocess.run(
            ["exiftool", "-sort", filepath.as_posix()],
            capture_output=True,
            check=False,
        )
        lines = output.stdout.decode()
        # logger.debug(lines)
        if self.exiftool is None:
            self.exiftool = {}
        rval = {}
        for line in lines.split("\n"):
            label = line.split(':')[0].strip()
            if label not in EXIFTOOL2PIL:
                continue
            if EXIFTOOL2PIL[label] in self.exiftool:
                # Override Create Date, as this seems to have time zone always (Z).
                if label != 'GPS Date/Time':
                    logger.warning("duplicate label: %r", EXIFTOOL2PIL[label])
                    continue
            if EXIFTOOL2PIL[label] in EXIF_INTS:
                val = (int(line[34:].strip()), 1)
            else:
                val = line[34:].strip()
            rval[EXIFTOOL2PIL[label][0]] = val
            self.exiftool[EXIFTOOL2PIL[label]] = val
        # logger.debug(pprint.pformat(self.exiftool))
        logger.debug(rval)
        return rval

    def _get_exif(self, filepath: Path) -> dict:
        """
        Get exif data via the first successful tool for the file.

        :raise ValueError: when no exif data can be extracted
        """
        ext = filepath.suffix.lower()
        logger.debug(ext)
        if ext in (".heic", ".heif"):
            # Apple's format is not supported in PIL (yet as of 2021-11)
            logger.debug("opening HEIF file %r", filepath)
            # cyheif's metadata is mediocre garbage
            # try:
            #     # pylint: disable=c-extension-no-member
            #     return cyheif.get_exif_data(filepath.as_posix().encode())
            # except:  # noqa pylint: disable=bare-except
            #     logger.debug("cyheif failed on %s", filepath)
            return self._exiftool(filepath)
        logger.debug("opening image file %r", filepath)
        try:
            im = Image.open(filepath)
            self.exiftool = None
            return im._getexif()  # pylint: disable=protected-access
        except:  # noqa pylint: disable=bare-except
            logger.debug("PIL failed on %s", filepath)
        try:
            return self._exiftool(filepath)
        except:  # noqa pylint: disable=bare-except
            logger.debug("exiftool failed on %s", filepath)
        try:
            # Hachoir has minimal metadata extraction, so try it last.
            parser = createParser(filepath.as_posix())
            metadata = extractMetadata(parser)
            return {"DateTime": metadata.get("creation_date")}
        except:  # noqa pylint: disable=bare-except
            raise ValueError("no metadata found")  # pylint: disable=W0707

    def _parse_timestamp(self, date_string: str) -> datetime:  # pylint: disable=R0201
        """
        Convert a metadata timestamp to a datetime.

        :param date_string: date and time as a string, or datetime returned as is
        :return: datetime instance
        """
        logger.debug(date_string)
        if isinstance(date_string, datetime):
            return date_string
        for ii in ('-', '+'):
            parts = date_string.split(ii, 1)
            if len(parts) == 2 and '.' in parts[0]:
                # turn milliseconds into nanoseconds, if we have fractional seconds
                ms = parts[0].split('.', 1)
                ns = f"{ms[1]:0<6}"[0:6]
                ds = f"{ms[0]}.{ns}{parts[1]}"
            else:
                ds = date_string
            for jj in ("%Y:%m:%d %H:%M:%S.%f%z", "%Y:%m:%d %H:%M:%S%z"):
                logger.debug("trying %s with %s", ds, jj)
                try:
                    return datetime.strptime(ds, jj)
                except ValueError:
                    pass
        logger.debug("trying fromisoformat")
        try:
            return datetime.fromisoformat(date_string)
        except ValueError:
            pass
        jj = "%Y:%m:%d %H:%M:%S%Z"
        logger.debug("trying with %s", jj)
        try:
            rval = datetime.strptime(date_string, jj)
            # Despite the presence of a timezone indicator, the above returns
            # a naive datetime. Convert it to timezone aware using pytz.
            rval = pytz.timezone(date_string[-3:]).localize(rval)
            logger.debug(rval)
            return rval
        except ValueError:
            pass
        try:
            logger.debug("trying localtime")
            # It would be better if the image has GPS data, to use that to get
            # the timezone, but assuming the local timezone at least prevents
            # returning a naive datetime object, which would be a problem in
            # get_date() if any other datetime is timezone aware.
            tz = time.strftime("%Z", time.localtime())
            logger.debug(tz)
            dt = date_string + tz
            logger.debug("trying %s with %s", dt, jj)
            rval = datetime.strptime(dt, jj)           # Same deal as the
            rval = pytz.timezone(tz).localize(rval)    # previous try.
            logger.debug(rval)
            return rval
        except ValueError:
            pass
        logger.warning("failed to parse %s", date_string)
        return None

    def _save_metadata(self, ex: dict):
        """
        Get all needed metadata from the exif dictionary.
        """
        if ex is None:
            logger.warning("got an unexpected None in _save_metadata")
            return
        logger.debug(len(ex))
        for kk, vv in ex.items():
            if kk in ('GPSLatitude', 'GPSLongitude'):
                self.metadata[kk[3:]] = convert_to_decimal(vv)
                continue
            if kk not in EXIF_TAGS:
                logger.debug("%r not found in ExifTags.TAGS", kk)
                continue
            logger.debug("%r: %r", kk, vv)
            if kk in TIME_KEYS:
                if '-' not in vv and '+' not in vv:
                    if 'OffsetTime' in ex:
                        self.metadata[kk] = self._parse_timestamp(vv + ex['OffsetTime'])
                    else:
                        self.metadata[kk] = self._parse_timestamp(vv + '+0000')
                else:
                    self.metadata[kk] = self._parse_timestamp(vv)
            if kk in ("ImageDescription", "XPTitle", "Latitude", "Longitude"):
                self.metadata[kk] = vv
        logger.debug(self.metadata)

    def _get_first_date(self):
        """
        Get the first available date.
        """
        for kk in TIME_KEYS:
            if kk in self.metadata:
                return [self.metadata[kk], ]
        return []

    def get_dates(self, filepath: Path) -> list[datetime]:
        """
        Get date from an image file.

        :param filepath: image file to read from
        :return: three timestamps or raise
        :raise: KeyError when no timestamp key is found
        """
        if not self.metadata:
            self._save_metadata(self._get_exif(filepath))
        logger.debug(pprint.pformat(self.metadata))
        rval = self._get_first_date()
        if not rval and self.exiftool is None:
            self._save_metadata(self._exiftool(filepath))
            rval = self._get_first_date()
        if not rval:
            logger.warning("none of %s found, so using file creation time", TIME_KEYS)
            rval.append(datetime.fromtimestamp(filepath.stat().st_mtime))
        # Add additional dates, if available.
        if "DateTimeOriginal" in self.metadata:
            rval.append(self.metadata["DateTimeOriginal"])
        else:
            rval.append(rval[0])
        if "DateTimeDigitized" in self.metadata:
            rval.append(self.metadata["DateTimeDigitized"])
        else:
            rval.append(rval[0])
        return rval

    def get_date(self, filepath: Path) -> datetime:  # pylint: disable=R0201
        """
        Get the earliest date from the image metadata.

        @return: year:month:date string
        """
        dates = self.get_dates(filepath)
        for ii in range(3):
            if dates[ii] is not None:
                d0 = dates[ii]
        if d0 is None:
            raise ValueError(f"No date found for {filepath.as_posix()}")
        d1 = dates[1] if len(dates) > 1 and dates[1] is not None else d0
        d2 = dates[2] if len(dates) > 2 and dates[2] is not None else d0
        rval = d0 if d0 < d1 else d1
        rval = d2 if d2 < rval else rval
        self.metadata['first_date'] = rval
        logger.debug(rval)
        return rval

    def make_path(self, base: Path, date: datetime) -> Path:
        """
        Make the output path, if necessary.

        @param base: base destination directory
        @param date: output of early_date(), datetime instance
        """
        dpath = base.joinpath(date.strftime("%Y"), date.strftime("%m"))
        logger.debug(dpath)
        if not self.args["month"]:
            dpath = dpath.joinpath(date.strftime("%d"))
            logger.debug(dpath)
        logger.debug(self.metadata)
        if self.args["geo_group"] and "Latitude" in self.metadata:
            self.get_geoname()
            if 'place' in self.metadata:
                if self.args["month"]:
                    dpath = dpath.joinpath(f"{date.strftime('%d')}-{self.metadata['place']}")
                else:
                    dpath = dpath.joinpath(self.metadata['place'])
        if not dpath.exists():
            logger.info("creating %s", dpath)
            dpath.mkdir(parents=True)
        else:
            logger.debug("using %s", dpath)
        return dpath

    def get_target(self, src: Path, dst: Path) -> Path:
        """
        Get the base destination plus metadata determined date directory full filename.

        @param src: source file
        @param dst: base destination directory (may be the same for just a rename)
        @return: full Path to the metadata and options determined destination
        """
        logger.debug("%r, %r", src, dst)
        date = self.get_date(src)
        # don't make a path, if we're just renaming a file
        tdir = self.make_path(dst, date) if src.parent != dst else dst
        fn = src.name
        if self.args["image_description"]:
            if "ImageDescription" in self.metadata:
                fn = self.metadata["ImageDescription"].replace(" ", "") + src.suffix
            elif "XPTitle" in self.metadata:
                fn = self.metadata["XPTitle"].replace(" ", "") + src.suffix
        if self.args["command"][0] == "rename" or self.args["rename"]:
            fn = f"{date.strftime('%Y-%m-%dT%H:%M')}-{fn}"
        rval = tdir.joinpath(fn)
        logger.debug(rval)
        if (
            (self.args['command'][0] == 'convert' or self.args['convert']) and
            rval.suffix.lower() in (".heic", ".heif")
        ):
            return rval.parent.joinpath(f"{rval.stem}.jpg")
        return rval

    def get_MD5(self, filepath: Path) -> str:  # pylint: disable=R0201
        """
        Get the MD5 for the given file.
        """
        logger.debug(filepath)
        hasher = hashlib.md5()
        with open(filepath, "rb") as afile:
            buf = afile.read(BLOCKSIZE)
            while len(buf) > 0:
                hasher.update(buf)
                buf = afile.read(BLOCKSIZE)
        return hasher.hexdigest()

    def check_source(self) -> bool:
        """
        If the since option is used, make sure the source is newer.

        :return: True if the file should be skipped
        """
        if not self.since:
            return False
        meta_date = self.metadata['first_date'].date()
        logger.debug("%r <= %r?", meta_date, self.since)
        if meta_date <= self.since:
            return True
        return False

    def check_target(self, src: Path, trgt: Path) -> bool:
        """
        If the target exists, either do nothing for the same target, delete
        no longer wanted input, or delete the existing output and continue.

        @param src: source file
        @param dst: destination file
        @return True when target exists, or False to continue processing
        """
        logger.debug("%r, %r", src, trgt)
        if not trgt.exists():
            return False
        if self.get_MD5(src) == self.get_MD5(trgt):
            logger.warning("%s already exists as the same file", trgt)
            if not self.args["dry_run"] and self.args['command'][0] == 'move':
                logger.info("deleting duplicate input file %s", src)
                src.unlink()
            return True
        logger.warning("%s exists, but is different than %s", trgt, src)
        if self.args['force']:
            logger.debug("deleting %s to continue processing", trgt)
            trgt.unlink()
        return not self.args['force']

    def convert_file(self, src: Path, jpeg: Path) -> bool:
        """
        Convert a less supported format to a more common format.

        :param src: input file name
        :param jpeg: output file name, from get_target
        """
        if src.suffix.lower() not in (".heic", ".heif"):
            return False
        logger.debug("converting %r to %r", src, jpeg)
        try:
            # pylint: disable=c-extension-no-member
            pil_img = cyheif.get_pil_image(src.as_posix().encode())
        except:  # noqa pylint: disable=bare-except
            logger.error("failed getting a PIL image from %r", src)
            return False
        pil_exif = piexif.load(pil_img.info['exif'])
        # logger.debug(pil_exif)
        for key, val in self.exiftool.items():
            for kk, vv in PIEXIF_MAP.items():
                ifd = getattr(piexif, vv)
                if getattr(ifd, key[0], False) and key[1] not in pil_exif[kk]:
                    logger.debug("%r, %r -> %r, %r -> %r", key, val, kk, vv, ifd)
                    if key[1] == 33434:
                        parts = val.split('/')
                        pil_exif[kk][key[1]] = (int(parts[0]), int(parts[1]))
                    elif isinstance(val, str):
                        pil_exif[kk][key[1]] = val.encode('utf-8')
                    else:
                        pil_exif[kk][key[1]] = val
        logger.debug(pprint.pformat({kk: vv for kk, vv in pil_exif.items()}))
        if self.args["dry_run"]:
            return False
        try:
            exif_bytes = piexif.dump(pil_exif)
            logger.debug("saving %s with %d bytes of exif", jpeg.as_posix(), len(exif_bytes))
            pil_img.save(jpeg.as_posix(), "JPEG", exif=exif_bytes)
            return True
        except Exception as cferr:  # noqa pylint: disable=broad-except
            logger.debug("failed changing %r to %r", src, jpeg)
            logger.error(cferr, exc_info=True)
            raise

    def copy_move(self, ff, cmd):
        """
        Copy or move the given file.
        """
        trgt = self.get_target(ff, Path(self.args["destination"]))
        if ff == trgt:
            logger.error("calculated target, %s, is the same as the source", trgt)
            return False
        if self.check_source() or self.check_target(ff, trgt):
            logger.debug("skipping %s for %s", cmd, trgt)
            return False
        if self.args['convert']:
            if self.convert_file(ff, trgt):
                if cmd == "move":
                    ff.unlink()
                return trgt
        elif not self.args["dry_run"]:
            if cmd == "copy":
                logger.info("copying %s to %s", ff, trgt)
                shutil.copy2(ff, trgt)
            else:
                logger.info("moving %s to %s", ff, trgt)
                shutil.move(ff, trgt)
            return trgt
        return False

    def copy(self, ff):
        """
        Copy files from source to dest.
        """
        return self.copy_move(ff, "copy")

    def move(self, ff):
        """
        Move files from source to dest.
        """
        return self.copy_move(ff, "move")

    def convert(self, ff):
        """
        Convert (Apple) files to more a common format.

        :param args: dictionary of command line options
        """
        ntrgt = self.get_target(ff, ff.parent)
        if self.check_source() or self.check_target(ff, ntrgt):
            logger.debug("not converting")
            return None
        if not self.args["dry_run"]:
            self.convert_file(ff, ntrgt)
        return ntrgt

    def rename(self, ff):
        """
        Rename files to YYYY-MM-DD_existing_file_name.

        :param args: dictionary of command line options
        """
        ntrgt = self.get_target(ff, ff.parent)
        if self.check_source() or self.check_target(ff, ntrgt):
            logger.debug("not renaming")
            return None
        logger.debug("%r to %r", ff, ntrgt)
        if not self.args["dry_run"]:
            ff.rename(ntrgt)
        return ntrgt

    def touch(self, ff) -> bool:
        """
        Touch files with earliest metadata date.
        """
        if 'first_date' not in self.metadata:
            self.get_date(ff)
        if self.check_source():
            return False
        logger.debug("%r to %s", ff, self.metadata['first_date'])
        if not self.args["dry_run"]:
            os.utime(ff, times=(self.metadata['first_date'].timestamp(),
                                self.metadata['first_date'].timestamp()))
        return False

    def main(self):
        """
        Loop through the input, processing the given command.
        """
        logger.debug("%s -> %s", self.args["source"], self.args["destination"])
        try:
            for ff in list(Path(self.args["source"]).rglob("*")):
                logger.debug(ff.as_posix())
                if ff.is_file():
                    self.metadata.clear()
                    self.exiftool.clear()
                    try:
                        trgt = getattr(self, self.args['command'][0])(ff)
                        logger.debug("%r and %r and not %r", trgt, self.args['touch'], self.args["dry_run"])
                        if trgt and self.args['touch'] and not self.args["dry_run"]:
                            self.touch(trgt)
                    except Exception as err:  # pylint: disable=broad-except,redefined-outer-name
                        logger.debug(err, exc_info=True)
                        logger.warning("skipping %r: %s", ff, err)
                        raise
        finally:
            if self.geodata:
                self.save_geodata()


def _get_source_path() -> Path:
    """
    Determine default input directory, checking Apple and Lineage OS device paths.
    """
    # /run/user/1023/gvfs/gphoto2:host=Apple_Inc._iPhone_00008030001E10AA34E3802E/DCIM
    try:
        m_base = os.path.join("/run/user", str(os.geteuid()), "gvfs")
        basep = Path(m_base)
        return os.path.join(m_base, list(basep.glob("gphoto*"))[0], "DCIM")
    except Exception as err:  # pylint: disable=broad-except
        logger.debug(err)
    try:
        m_base = os.path.join("/run/user", str(os.geteuid()), "gvfs")
        basep = Path(m_base)
        return os.path.join(m_base, list(basep.glob("mtp*"))[0], "Internal shared storage", "DCIM", "Camera")
    except Exception as err:  # pylint: disable=broad-except
        logger.debug(err)
    return os.environ["PWD"]

if __name__ == "__main__":
    import argparse
    m_input = _get_source_path()
    m_output = os.path.join(os.environ["HOME"], "Pictures")
    m_parser = argparse.ArgumentParser(description="Manage photo and video files")
    m_parser.add_argument("-D", "--debug", action="store_true")
    m_parser.add_argument(
        "-d", "--dry-run", action="store_true", help="do not actually modify files"
    )
    m_parser.add_argument(
        "-S", "--scan-dirs", action="store_true", help="scan output directories for location information"
    )
    m_parser.add_argument(
        "-f", "--force", action="store_true", help="force overwriting existing output"
    )
    m_parser.add_argument(
        "-c",
        "--convert",
        action="store_true",
        help="convert HEIF to JPEG, in addition to copy or move of original",
    )
    m_parser.add_argument(
        "-r",
        "--rename",
        action="store_true",
        help="rename files to YYYY-MM-DD_existing_file_name, in addition to copy or move",
    )
    m_parser.add_argument(
        "-i",
        "--image-description",
        action="store_true",
        help="rename files to the ImageDescription or XPTitle if defined."
        " Replaces existing file name. Can be combined with -r.",
    )
    m_parser.add_argument(
        "-t",
        "--touch",
        action="store_true",
        help="set file dates to earliest date in image metadata, in addition to copy or move",
    )
    m_parser.add_argument(
        "-m",
        "--month",
        action="store_true",
        help="copy or move to month directories (YYYY/MM) rather than day (YYYY/MM/DD)",
    )
    m_parser.add_argument(
        "-g",
        "--geo-group",
        action="store_true",
        help="copy or move to town name based subdirectories (YYYY/MM/DD-[Town]",
    )
    m_parser.add_argument(
        "-s",
        "--since",
        help="YYYY-MM-DD format date that all pictures must come after",
    )
    m_parser.add_argument(
        "command",
        nargs=1,
        choices=["copy", "move", "convert", "rename", "touch"],
        help="copy and move need source & destination directories, others are in place (1 directory)",
    )
    m_parser.add_argument(
        "source",
        nargs="?",
        default=m_input,
        help=f"source of files to operate on (default: {m_input})",
    )
    m_parser.add_argument(
        "destination",
        nargs="?",
        default=m_output,
        help=f"where to copy or move files to (default: {m_output})",
    )
    m_args = m_parser.parse_args()
    if m_args.debug:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
    PhotoFileMan(vars(m_args)).main()
