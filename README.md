# photo-file-manager

I wanted a bit more functionality than just copying files from my phone into
~/Pictures. To name a few things:

* Put files in year/month directories, or year/month/day.
* If GPS data is in the file, look up place names and put files in
  year/month/day-location directories.
* Touch files so that the file system date is the date of the picture.
* Convert HEIF (iPhone) files into JPG.
* Automatically figure out the source location when an iPhone or LineageOS phone
  is attached and unlocked.

The photo-file-manager.py script does that for me. YMMV.

# Requirements

On a Debian based system (including Ubuntu and Linux Mint):

    apt install python3 python3-pip python3-piexif python3-pil python3-shapely python3-pytzdata libimage-exiftool-perl
    pip install cyheif hachoir

# Installation

Copy photo-file-manager.py to a PATH directory.

Yes, this should be automated, but that's what it is right now.

# Usage

usage: photo-file-manager.py [-h] [-D] [-d] [-S] [-f] [-c] [-r] [-i] [-t] [-m] [-g] [-s SINCE]
                             {copy,move,convert,rename,touch} [source] [destination]

Manage photo and video files

positional arguments:
  {copy,move,convert,rename,touch}
                        copy and move need source & destination directories, others are in place (1 directory)
  source                source of files to operate on (default: /run/user/1023/gvfs/mtp:host=OnePlus_KB2003_dd820321/Internal shared
                        storage/DCIM/Camera)
  destination           where to copy or move files to (default: /home/alan/Pictures)

options:
  -h, --help            show this help message and exit
  -D, --debug
  -d, --dry-run         do not actually modify files
  -S, --scan-dirs       scan output directories for location information
  -f, --force           force overwriting existing output
  -c, --convert         convert HEIF to JPEG, in addition to copy or move of original
  -r, --rename          rename files to YYYY-MM-DD_existing_file_name, in addition to copy or move
  -i, --image-description
                        rename files to the ImageDescription or XPTitle if defined. Replaces existing file name. Can be combined with -r.
  -t, --touch           set file dates to earliest date in image metadata, in addition to copy or move
  -m, --month           copy or move to month directories (YYYY/MM) rather than day (YYYY/MM/DD)
  -g, --geo-group       copy or move to town name based subdirectories (YYYY/MM/DD-[Town]
  -s SINCE, --since SINCE
                        YYYY-MM-DD format date that all pictures must come after