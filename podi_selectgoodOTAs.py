#! /usr/bin/env python
#
# Copyright 2012-2013 Ralf Kotulla
#                     kotulla@uwm.edu
#
# This file is part of the ODI QuickReduce pipeline package.
#
# If you find this program or parts thereof please make sure to
# cite it appropriately (please contact the author for the most
# up-to-date reference to use). Also if you find any problems 
# or have suggestiosn on how to improve the code or its 
# functionality please let me know. Comments and questions are 
# always welcome. 
#
# The code is made publicly available. Feel free to share the link
# with whoever might be interested. However, I do ask you to not 
# publish additional copies on your own website or other sources. 
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. 
#

import sys
import os
import astropy.io.fits as pyfits
import numpy
from podi_definitions import *
from podi_commandline import *

def load_exthead(fitsfile, filelist):
    extheads = []

    for headname in filelist:
        filename = fitsfile[:-4]+headname
        if (not os.path.exists(filename)):
            continue
        
        exthead = [None]
        eh = open(filename)
        lines = eh.readlines()

        this_extension = []
        for line in lines:
            items = line.split()
            
            this_extension.append(line.strip())
            if (items[0] == "END"):
                exthead.append(this_extension)
                this_extension = []

        extheads.append(exthead)

#    print len(extheads[0])
    return extheads


if __name__ == "__main__":

    cmdline = get_clean_cmdline()

    filename = cmdline[1]
    outfits = cmdline[2]

    stdout_write("Reading %s ..." % (filename))

    hdulist = pyfits.open(filename)
    out_list = [hdulist[0]]

    if (os.path.isfile(outfits) and cmdline_arg_isset("-noclobber")):
        print " file exists, skipping"
        sys.exit(0)


    extheads = []
    exthead_names = cmdline[3:]
    extheads = load_exthead(filename, exthead_names)

    #
    # Create all output exthead files for swarp
    #
    exthead_files = []
    for exthead in exthead_names:
        output_name = outfits[:-4]+exthead
        file = open(output_name, "w")
        exthead_files.append(file)

    #
    # Now go through the list of extensions, select only the central 3x3 
    # and only ones not marked as video cells
    #
    
    #print "# ext.headers =",len(extheads)
    #print "# ext.header files =",len(exthead_files)

    for extension in range(1, len(hdulist)):
            
        extname = hdulist[extension].header['EXTNAME']
        if (extname[0:3] != "OTA" or extname[-3:] != "SCI"):
            continue

        cellmode = hdulist[extension].header['CELLMODE']
        if (cellmode.find("V") >= 0):
            continue

        ota_x = int(extname[3])
        ota_y = int(extname[4])
        if (ota_x >= 2 and ota_x <= 4 and ota_y >= 2 and ota_y <= 4):
            out_list.append(hdulist[extension])

            # Also make sure to add this extension to the external header file
            for i in range(len(extheads)):
                file = exthead_files[i]
                file.write(os.linesep.join(extheads[i][extension]))
                file.write("\n")

    # Close all external header files
    for file in exthead_files:
        file.close()

    out_hdulist = pyfits.HDUList(out_list)
    stdout_write(" writing %s ..." % (outfits))
    out_hdulist.writeto(outfits, overwrite=True)
    stdout_write(" done!\n")

