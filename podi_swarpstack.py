#!/usr/bin/env python
#
# Copyright 2012-2013 Ralf Kotulla & WIYN Observatory
#                     University of Wisconsin - Milwaukee & Madison
#                     kotulla@uwm.edu
#
# This file is part of the ODI QuickReduce pipeline package.
#
# If you find this program or parts thereof please make sure to
# cite it appropriately (please contact the author for the most
# up-to-date reference to use). Also if you find any problems 
# or have suggestions on how to improve the code or its 
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

"""

Function
--------------------------------------------------------------------------------

**podi_swarpstack.py** handles most of the most common stacking problems. It 
supports a number of use-cases, from simle stacking a bunch of files, adding
more frames to an additional stack, as well as matching a new frame to a 
reference stack.

The general procedure is to create a single rectified image for each of the
input frames first. In a second step, swarp combines all these rectified images
into the final step. While this is overkill for stellar fields, this approach
ensures better sky background subtraction across OTAs in the case of large
galaxies or objects. 

Before each run, swarpstack determines the final size of the resulting stacked
frame. Each of the input frames is then rectified, distortion corrected and
interpolated onto this final grid, eliminating double-interpolations.



How to run podi_swarpstack
--------------------------------------------------------------------------------

Simple stacking of a bunch of frames

    podi_swarpstack.py output_stack.fits file1.fits file2.fits


Adding frames to an already existing stack

    podi_swarpstack.py -add output_stack.fits file3.fits

Matching a new frame to a reference frame

    podi_swarpstack.py -reference=reference.fits output_stack.fits file*.fits



Additional command line options
--------------------------------------------------------------------------------

* **-bgsub**

  Activate background subtraction. Without this frame no background subtraction
  is performed. While this is the safest options for some cases it leads to 
  rather ratty looking stacks if the sky-level was at all variable during the 
  exposure (e.g. with moon, twilight, scattered light)


* **-reusesingles**

  By default, the single recified images are re-created when swarpstack is being
  run. With this option swarpstack checks if a recitified image already exists
  and uses the existing file instead of re-creating a new one. This is
  particularly helpful and in fact the default if adding new frames to an
  already existing stack.

  
* **-dimension=X**

  If given without the -bgsub parameter this option has no effect. If -bgsub is
  enabled, the background filtering options are adjusted automatically to avoid
  over-subtracting objects of the specified dimenson or smaller, hence avoiding
  subtracting the galacy continuum as background fluctuation. The value X
  specifies the galaxy/source dimension in arc-minutes.

* **-pixelscale=X**

  Chooses the specified pixel scale for the output stack, with X given in 
  arcsec/pixel.

* **-nonsidereal=a,b,c**

  Apply a non-sidereal motion correction to all input frames before stacking.
  a is the motion in dRA*cos(dec) in arcseconds/hour, b is the motion dDec, also 
  in arcsec/hour, and c is the MJD reference, either a valid FITS frame or the
  MJD as floating point number

"""


import os
import sys
import pyfits
import subprocess
import math

from podi_commandline import *
from podi_definitions import *
#from podi_collectcells import *
import podi_sitesetup as sitesetup
import podi_illumcorr
import multiprocessing

import podi_logging
import logging
import socket
import tempfile
import shutil
import warnings
import time

try:
    sys.path.append(sitesetup.exec_dir+"/test")
    import ephemerides
    import podi_ephemerides
except:
    pass

def mp_prepareinput(input_queue, output_queue, swarp_params, options):

    while (True):

        cmd = input_queue.get()
        if (cmd == None):
            input_queue.task_done()
            break

        (input_file, fileid) = cmd
        logger = logging.getLogger("MP-Prep( %s )" % (os.path.basename(input_file)))
        
        ret = {
            "master_reduction_files": {},
            "corrected_file": None,
            'exptime': 0,
            'mjd_obs_start': 0,
            'mjd_obs_end': 0,
            'nonsidereal-dradec': None,
        }

        try:
            hdulist = pyfits.open(input_file)
        except IOError:
            logger.error("Can't open file %s" % (input_file))
            output_queue.put(None)
            input_queue.task_done()
            continue

        mjd_obs_start = hdulist[0].header['MJD-OBS']
        exptime = hdulist[0].header['EXPMEAS'] if 'EXPMEAS' in hdulist[0].header else \
                  hdulist[0].header['EXPTIME']
        mjd_obs_end = hdulist[0].header['MJD-OBS'] + (exptime/86400.)

        # Save these values for the return queue
        ret['exptime'] = exptime
        ret['mjd_obs_start'] = mjd_obs_start
        ret['mjd_obs_end'] = mjd_obs_end

        corrected_filename = None

        # Keep track of what files are being used for this stack
        master_reduction_files_used = collect_reduction_files_used(
            {}, {"calibrated": input_file} ) #ret['corrected_file']})

        #
        # Compute on how to scale the flux values
        #
        fluxscale_value = numpy.NaN
        magzero = hdulist[0].header['PHOTZP_X'] if 'PHOTZP_X' in hdulist[0].header else -99.
        if (magzero > 0 and not swarp_params['no-fluxscale']):
            fluxscale_value = math.pow(10, 0.4*(swarp_params['target_magzero']-magzero))
        else:
            exptime = hdulist[0].header['EXPMEAS'] if 'EXPMEAS' in hdulist[0].header else (
                hdulist[0].header['EXPTIME'] if 'EXPTIME' in hdulist[0].header else 1.0)
            fluxscale_value = 1./exptime

        # Assemble the temporary filename for the corrected frame
        suffix = None
        # Now construct the output filename
        if (suffix == None and swarp_params['no-fluxscale'] and not numpy.isnan(fluxscale_value)):
            suffix = "exptimenorm"
        if (suffix == None and not numpy.isnan(swarp_params['target_magzero']) and not numpy.isnan(fluxscale_value)):
            suffix = "fluxnorm"
        if (suffix == None and swarp_params['use_nonsidereal']):
            suffix = "nonsidereal"
        if (suffix == None and swarp_params['use_ephemerides']):
            suffix = "ephemerides"
        if (suffix == None and options['skip_otas'] != []):
            suffix = "otaselect"
        if (suffix == None and options['illumcorr']):
            suffix = "illumcorr"
        if (suffix == None and not options['bpm_dir'] == None):
            suffix = "bpmfixed"
        if (suffix == None and not swarp_params['subtract_back'] == 'swarp'):
            suffix = "skysub"

        if (not suffix == None):
            corrected_filename = "%(single_dir)s/%(obsid)s.%(suffix)s.%(fileid)d.fits" % {
                "single_dir": swarp_params['unique_singledir'],
                "obsid": hdulist[0].header['OBSID'],
                "suffix": suffix,
                "fileid": fileid,
            }

        logger.info("Applying preparations ...")

        #
        # Check if we need to apply any corrections
        #
        if (corrected_filename == None or 
            (os.path.isfile(corrected_filename) and swarp_params['reuse_singles'])
        ):
            # Either we don't need to apply any corrections or we can re-use an 
            # older file with these corrections already applied
            logger.info("No correction needs to be applied!")
            ret['corrected_file'] = input_file
        else:
            gain = hdulist[0].header['GAIN']

            if (swarp_params['use_nonsidereal']):
                logger.debug("Applying the non-sidereal motion correction")

                # First apply the non-sidereal correction to all input frames
                # Keep frames that are already modified
                from podi_collectcells import apply_nonsidereal_correction
                # print options['nonsidereal']
                nonsidereal_dradec = apply_nonsidereal_correction(hdulist, options, logger)
                ret["nonsidereal-dradec"] = nonsidereal_dradec

                try:
                    if (os.path.isfile(options['nonsidereal']['ref'])):
                        master_reduction_files_used = collect_reduction_files_used(
                            master_reduction_files_used, 
                            {"nonsidereal-reference": options['nonsidereal']['ref']})
                except:
                    pass

            if (swarp_params['use_ephemerides']):
                # get MJD of current frame
                mjd_thisframe = hdulist[0].header['MJD-OBS'] + 0.5*hdulist[0].header['EXPTIME']/2./86400
                mjd_ref = swarp_params['ephemerides']['ref-mjd']

                # print "\n"*10
                # print "ref-mjd",mjd_ref
                # print "this mjd",mjd_thisframe
                # print "min mjd", numpy.min(swarp_params['ephemerides']['data'][:,0])
                # print "max mjd", numpy.max(swarp_params['ephemerides']['data'][:,0])
                # print "\n"*10

                # now compute the Ra/Dec of the target in both the reference 
                # frame and in this frame
                ephem_data = swarp_params['ephemerides']['data']
                ra_from_mjd = scipy.interpolate.interp1d( ephem_data[:,0], ephem_data[:,1], kind='linear' )
                dec_from_mjd = scipy.interpolate.interp1d( ephem_data[:,0], ephem_data[:,2], kind='linear' )

                ra_ref = ra_from_mjd(mjd_ref)
                ra_this = ra_from_mjd(mjd_thisframe)

                dec_ref = dec_from_mjd(mjd_ref)
                dec_this = dec_from_mjd(mjd_thisframe)

                # print "\n"*5, "ra//dec = ", ra_ref, dec_ref, "\n"*5
                # The Ra/Dec correction is how much the object has moved 
                # (as derived from the ephemerides) between the reference MJD 
                # and the timestamp of this frame
                d_ra = ra_ref - ra_this
                d_dec = dec_ref - dec_this
                # print d_ra, d_dec
                d_days = mjd_thisframe - mjd_ref
                logger.debug("Applying ephemerid correction (%+.6f deg, %+.6f deg, dT=%.3f days)" % (
                    d_ra, d_dec, d_days))

                # Now apply these corrections to all extensions with an
                # apparently valid WCS system
                orig_ra, orig_dec = None, None
                for ext in hdulist:
                    if ('CRVAL1' in ext.header and
                        'CRVAL2' in ext.header):
                        # print ext.header['CRVAL1'], ext.header['CRVAL2'], (ext.header['EXTNAME'] if 'EXTNAME' in ext.header else "??")
                        orig_ra = ext.header['CRVAL1'] if orig_ra == None else orig_ra
                        orig_dec = ext.header['CRVAL2'] if orig_dec == None else orig_dec
                        ext.header['CRVAL1'] += d_ra
                        ext.header['CRVAL2'] += d_dec
                        # print ext.header['CRVAL1'], ext.header['CRVAL2'], (ext.header['EXTNAME'] if 'EXTNAME' in ext.header else "??")

                logger.debug("Pre-correction Ra/Dec was: %12.7f  %+12.7f" % (orig_ra, orig_dec))
                logger.debug("Post-corrected Ra/Dec is: %12.7f %+12.7f" % (orig_ra + d_ra, orig_dec + d_dec))
                
                ret["nonsidereal-dradec"] = numpy.array([numpy.NaN, -d_dec, -d_ra])


            if (options['skip_otas'] != []):
                logger.debug("Skipping some OTAs")
                ota_list = []
                for ext in hdulist:
                    ota = -1
                    try:
                        ota = int(ext.header['EXTNAME'][3:5])
                    except:
                        pass
                    if (ota in options['skip_otas']):
                        logger.debug("skipping ota %s as requested" % (ext.header['EXTNAME']))
                        continue
                    ota_list.append(ext)

                # Save the modified OTA list for later
                hdulist = pyfits.HDUList(ota_list)

            if (not options['bpm_dir'] == None):
                logger.debug("Applying bad-pixel masks")
                for ext in range(len(hdulist)):
                    if (not is_image_extension(hdulist[ext])):
                        continue

                    fppos = None
                    if ('FPPOS' in hdulist[ext].header):
                        fppos = hdulist[ext].header['FPPOS']
                    if (not fppos == None):
                        region_file = "%s/bpm_%s.reg" % (options['bpm_dir'], fppos)
                        if (os.path.isfile(region_file)):
                            mask_broken_regions(hdulist[ext].data, region_file)
                            master_reduction_files_used = collect_reduction_files_used(
                                master_reduction_files_used, {"bpm": region_file})


            # Loop over all extensions and only select those that are not marked as guide chips
            if (True): #options['skip_otas'] != []):
                logger.debug("Sorting out guide-OTAs")
                ota_list = []
                for ext in hdulist:
                    if ('CELLMODE' in ext.header and
                        ext.header['CELLMODE'].find("V") >= 0):
                        logger.debug("skipping ota %s as requested" % (ext.header['EXTNAME']))
                        continue
                    ota_list.append(ext)

                # Save the modified OTA list for later
                hdulist = pyfits.HDUList(ota_list)

            if (not options['illumcorr_dir'] == None):
                illum_file = podi_illumcorr.get_illumination_filename(
                    options['illumcorr_dir'], hdulist[0].header['FILTER'], hdulist[0].header['BINNING'])
                logger.debug("Applying illumination correction (%s)" % (illum_file))
                master_reduction_files_used = collect_reduction_files_used(
                    master_reduction_files_used, {"illumination": illum_file})
                podi_illumcorr.apply_illumination_correction(hdulist, illum_file)

            skylevel = 0.
            if (not swarp_params['subtract_back'] == 'swarp' and not swarp_params['subtract_back'] == False):
                skylevel = numpy.NaN
                if (swarp_params['subtract_back'] in hdulist[0].header):
                    skylevel = hdulist[0].header[swarp_params['subtract_back']]
                else:
                    try:
                        skylevel = float(swarp_params['subtract_back'])
                    except ValueError:
                        logger.warning("Could not determine sky-level (%s), skipping sky-subtraction" % (
                            swarp_params['subtract_back']))
                    except:
                        raise
                if (not numpy.isnan(skylevel)):
                    for ext in hdulist:
                        if (not is_image_extension(ext)):
                            continue
                        ext.data -= skylevel
                        logger.debug("Subtracting skylevel (%f) from extension %s" % (skylevel, ext.name))
            elif (swarp_params['subtract_back'] == 'swarp'):
                skylevel = hdulist[0].header['SKYLEVEL']
            else:
                skylevel = hdulist[0].header['SKYLEVEL']

            if (not numpy.isnan(fluxscale_value)):
                logger.debug("Applying flux-scaling (%.10e)" % (fluxscale_value))
                for ext in hdulist:
                    if (not is_image_extension(ext)):
                        continue
                    ext.data *= fluxscale_value
                    logger.debug("Applying flux-scaling (%.10e) to extension %s" % (fluxscale_value, ext.name))

                # Apply fluxscaling to GAIN and SKYLEVLE as well
                gain /= fluxscale_value
                skylevel *= fluxscale_value

            # Check if the corrected file already exists - if not create it
            #if (not os.path.isfile(corrected_filename)):
            logger.debug("Writing correctly prepared file--> %s" % (corrected_filename))

            clobberfile(corrected_filename)
            hdulist.writeto(corrected_filename, clobber=True)

            # Now change the filename of the input list to reflect 
            # the corrected file
            ret['corrected_file'] = corrected_filename
            ret['gain'] = gain
            ret['skylevel'] = skylevel

        #
        # Now also create a relative weight map for this frame
        # scaling factor is the exposure time of each frame
        #
        weight_hdulist = [hdulist[0]] # copy the primary header
        for ext in hdulist[1:]:
            if (not is_image_extension(ext)):
                continue

            weight_data = numpy.ones(ext.data.shape, dtype=numpy.float32) #* 100.
            ret['weight'] = 1.
            if (not numpy.isnan(fluxscale_value)):
                weight_data /= fluxscale_value
                ret['weight'] = 1./fluxscale_value
            weight_data[numpy.isnan(ext.data)] = 0.

            weight_img = pyfits.ImageHDU(header=ext.header, data=weight_data) 
            weight_hdulist.append(weight_img)


        # convert extension list to proper HDUList ...
        weight_hdulist = pyfits.HDUList(weight_hdulist)

        # ... and write extension to file
        weight_filename = ret['corrected_file'][:-5]+".weight.fits"
        clobberfile(weight_filename)
        weight_hdulist.writeto(weight_filename, clobber=True)
        logger.info("Wrote input weight map to %s" % (weight_filename))

        # Finally, close the input file
        hdulist.close()

        #
        # Now we have the filename of the file to be used for the swarp-input
        #
        ret["master_reduction_files"] = master_reduction_files_used

        logger.debug("Sending return value to master process")
        output_queue.put(ret)
        input_queue.task_done()

        # print input_file, "\n", ret["master_reduction_files"]

    # end of routine



def prepare_input(inputlist, swarp_params, options):

    logger = logging.getLogger("PrepFiles")

    #
    # initialize queues for commands and return-values
    #
    in_queue = multiprocessing.JoinableQueue()
    out_queue = multiprocessing.Queue()

    #
    # fill queue with files to be processed
    #
    n_jobs = 0
    existing_inputlist = []
    for fn in inputlist: #i in range(len(inputlist)):
        if (not os.path.isfile(fn)):
            continue
        try:
            hdulist = pyfits.open(fn)
        except IOError:
            logger.error("Can't open file %s" % (fn))
            continue
        
        hdulist.close()
        existing_inputlist.append(fn)

    if (len(existing_inputlist) <= 0):
        logger.error("No valid files found")
        return

    wcs_inputlist = []
    photcal_inputlist = []
    for idx, fn in enumerate(existing_inputlist):
        hdulist = pyfits.open(fn)
        #
        # Perform some checks to only include valid frames in the stack
        #
        # Frame needs to have valid WCS solution
        if ('WCSCAL' in hdulist[0].header and
            not hdulist[0].header['WCSCAL'] and 
            not swarp_params['ignore_quality_checks']):
            logger.info("Excluding frame (%s) due to faulty WCS calibration" % (fn))
            #good_inputlist[idx] = None
            continue

        wcs_inputlist.append(fn)

        # and proper photometric calibration
        if ('MAGZERO' in hdulist[0].header and
            hdulist[0].header['MAGZERO'] <= 0 and
            not swarp_params['no-fluxscale'] and
            not swarp_params['ignore_quality_checks']):
            logger.info("Excluding frame (%s) due to missing photometric calibration" % (fn))
            #good_inputlist[idx] = None
            continue

        photcal_inputlist.append(fn)

    if (len(photcal_inputlist) > 0):
        logger.debug("Restricting input file list to files with valid photoemtric calibration")
        inputlist = photcal_inputlist
    elif (len(wcs_inputlist) > 0):
        logger.warning("No files with photometric calibration found, reverting to list of WCS-calibrated files")
        inputlist = wcs_inputlist
    else:
        logger.warning("No files with WCS and/or photometry found, reverting to unfiltered inputlist")
        inputlist = existing_inputlist

    for idx, fn in enumerate(inputlist):
        in_queue.put((fn, idx+1))
        n_jobs += 1

    logger.info("Queued %d jobs ..." % (n_jobs))

    #
    # Start worker processes
    #
    worker_args = (in_queue, out_queue, swarp_params, options)
    processes = []
    for i in range(sitesetup.number_cpus):
        p = multiprocessing.Process(target=mp_prepareinput, args=worker_args)
        p.start()
        processes.append(p)

        # also add a quit-command for each process
        in_queue.put(None)
        
    #
    # wait until all work is done
    #
    in_queue.join()

    #
    # return the list of corrected files.
    #

    # Keep track of what files are being used for this stack
    master_reduction_files_used = {}
    corrected_file_list = []
    nonsidereal_offsets = []

    stack_start_time = 1e9
    stack_end_time = -1e9
    stack_total_exptime = 0
    stack_framecount = 0

    gain_list = numpy.zeros((n_jobs))
    skylevel_list = numpy.zeros((n_jobs))
    weight_list = numpy.zeros((n_jobs))

    for i in range(n_jobs):
        ret = out_queue.get()
        logger.debug("Received results from job %d" % (i+1))

        gain_list[i] = ret['gain']
        skylevel_list[i] = ret['skylevel']
        weight_list[i] = ret['weight']

        # Also set some global stack-related parameters that we will add to the 
        # final stack at the end
        # mjd_obs_start = hdulist[0].header['MJD-OBS']
        # exptime = hdulist[0].header['EXPMEAS'] if 'EXPMEAS' in hdulist[0].header else \
        #           hdulist[0].header['EXPTIME']
        # mjd_obs_end = hdulist[0].header['MJD-OBS'] + (exptime/86400.)

        # logger.debug("Exposure time: %f, MJD=%f" % (exptime, mjd_obs_start))

        stack_total_exptime += ret['exptime']
        stack_framecount += 1

        stack_start_time = numpy.min([stack_start_time, ret['mjd_obs_start']])
        stack_end_time = numpy.max([stack_end_time, ret['mjd_obs_end']])

        master_reduction_files_used = collect_reduction_files_used(master_reduction_files_used, 
                                                                   ret['master_reduction_files'])

        corrected_file_list.append(ret['corrected_file'])
        nonsidereal_offsets.append(ret['nonsidereal-dradec'])

    photom_list = (gain_list, skylevel_list, weight_list)

    #
    # By now all frames have all corrections applied,
    # so we can go ahead and stack them as usual
    #
    
    # Make sure to join/terminate all processes
    for p in processes:
        p.join()
        
    logger.info("All files prepared!")

    return (corrected_file_list, 
            stack_total_exptime, 
            stack_framecount, 
            stack_start_time, 
            stack_end_time, 
            master_reduction_files_used,
            nonsidereal_offsets,
            photom_list)



def cleanup_singles(unique_singledir, logger):

    try:
        shutil.rmtree(unique_singledir)
    except:
        logger.error("There was a problem with recursively deleting the temp directory")
        podi_logging.log_exception()
        pass

    return



def mp_swarp_single(sgl_queue, dum):

    while(True):
        cmd = sgl_queue.get()
        if (cmd == None):
            sgl_queue.task_done()
            break

        swarp_cmd, prepared_file, single_file, swarp_params, nonsidereal_dradec, create_mask = cmd
        logger = logging.getLogger("MPSwarpSgl(%s)" % (os.path.basename(single_file)))

        hdulist = pyfits.open(prepared_file)
        obsid = hdulist[0].header['OBSID']
        logger.info("Starting work on focal-plane frame ...")

        single_created_ok = False
        try:
            logger.debug(" ".join(swarp_cmd.split()))
            ret = subprocess.Popen(swarp_cmd.split(), 
                                   stdout=subprocess.PIPE, 
                                   stderr=subprocess.PIPE)
            (swarp_stdout, swarp_stderr) = ret.communicate()
            if (sitesetup.log_shell_output):
                logger.debug("\nCommand:\n%s\n--> Returncode: %d\n---\nStd.Out:\n%s\n---\nStd.Err:\n%s\n---", 
                             swarp_cmd, ret.returncode, swarp_stdout, swarp_stderr)
            # logger.debug("swarp stdout:\n"+swarp_stdout)
            # if (len(swarp_stderr) > 0 and ret.returncode != 0):
            #     logger.warning("swarp stderr:\n"+swarp_stderr)
            # elif (sitesetup.log_shell_output):
            #     logger.debug("swarp stderr:\n"+swarp_stderr)

            # Add some basic headers from input file to the single file
            # this is important for the differencing etc.
            logger.info("adding header to single file (%s)" % (single_file))
            hdu_single = pyfits.open(single_file,  mode='update')
            for hdrkey in [
                    'TARGRA', 'TARGDEC',
                    'FILTER', 'FILTERID', 'FILTDSCR', 
                    'OBSID', 'OBJECT', 
                    'EXPTIME',
                    'DATE-OBS', 'TIME-OBS', 'MJD-OBS']:
                if (hdrkey in hdulist[0].header):
                    key, val, com = hdulist[0].header.cards[hdrkey]
                    hdu_single[0].header[key] = (val, com)
            # hdu_single.writeto(single_file, clobber=True)
            hdu_single.flush()
            hdu_single.close()

            single_created_ok = True
            #print "\n".join(swarp_stderr)
            # single_prepared_files.append(single_file)
        except OSError as e:
            podi_logging.log_exception()
            print >>sys.stderr, "Execution failed:", e

        #
        # Apply the mask if requested
        #
        if (single_created_ok and create_mask and swarp_params['mask-list']):
            # not swarp_params['mask'] == None and 

            #
            # Based on the MJD of this frame, determine the best mask-frame
            #
            try:
                frame_mjd = hdulist[0].header['MJD-OBS']
            except:
                frame_mjd = 0
            diff_mjd = numpy.fabs(swarp_params['mask-mjds'] - frame_mjd)
            # Find the frame with the smallest delta_mjd
            idx = numpy.argmin(diff_mjd)
            mask_file = swarp_params['mask-list'][idx]
            logger.info("Using mask %s for frame %s" % (mask_file, prepared_file))
 
            #
            # Apply the non-sidereal correction to the mask
            # (only in non-sidereal mode)
            #
            this_mask = single_file[:-5]+".maskraw.fits"
            logger.info("This mask: %s" % (this_mask))
            mask_hdu = pyfits.open(mask_file) #swarp_params['mask'])

            logger.info("Applying non-sid corr: %s" % (str(nonsidereal_dradec)))
            if (not nonsidereal_dradec == None):
                
                d_radec = numpy.array(nonsidereal_dradec)

                # Correct the declination
                mask_hdu[0].header['CRVAL2'] -= d_radec[1]
                # correct RA, compensating for cos(declination)

                if (d_radec.shape[0] == 3):
                    mask_hdu[0].header['CRVAL1'] -= d_radec[2]
                else:
                    cos_dec = math.cos(math.radians(mask_hdu[0].header['CRVAL2']))
                    mask_hdu[0].header['CRVAL1'] -= d_radec[0] / cos_dec

            clobberfile(this_mask)
            mask_hdu.writeto(this_mask, clobber=True)
            mask_hdu.close()
            logger.info("wrote raw mask with fudged WCS: %s" % (this_mask))

            #
            # Swarp the mask to the identical pixelgrid as the single frame
            #
            hdu_single = pyfits.open(single_file,  mode='update')
            out_crval1 = hdu_single[0].header['CRVAL1']
            out_crval2 = hdu_single[0].header['CRVAL2']
            out_naxis1 = hdu_single[0].header['NAXIS1']
            out_naxis2 = hdu_single[0].header['NAXIS2']
            hdu_single.close()

            mask_aligned = single_file[:-5]+".mask.fits"
            swarp_mask = """
                %(swarp)s -c %(swarp_default)s 
                -IMAGEOUT_NAME %(mask_aligned)s
                -WEIGHTOUT_NAME /dev/null
                -CENTER_TYPE MANUAL
                -CENTER %(center_ra)f,%(center_dec)f
                -IMAGE_SIZE %(imgsizex)d,%(imgsizey)d
                -RESAMPLE_DIR %(resample_dir)s
                -RESAMPLING_TYPE BILINEAR
                -PIXEL_SCALE %(pixelscale)f \
                -PIXELSCALE_TYPE %(pixelscale_type)s \
                -COMBINE Y \
                -COMBINE_TYPE AVERAGE \
                -SUBTRACT_BACK N \
                %(mask_raw)s
            """ % {
                'swarp': sitesetup.swarp_exec,
                'swarp_default': "%s/.config/swarp.default" % (sitesetup.exec_dir),
                'mask_raw': this_mask,
                'mask_aligned': mask_aligned,
                'center_ra': out_crval1,
                'center_dec': out_crval2,
                'imgsizex': out_naxis1,
                'imgsizey': out_naxis2,
                'resample_dir': swarp_params['unique_singledir'],
                'pixelscale': swarp_params['pixelscale'],
                'pixelscale_type': "MANUAL",
                }

            # print "\n"*3," ".join(swarp_mask.split()),"\n"*3
            logger.info("Matching global mask to frame ...")
            try:
                start_time = time.time()
                logger.debug("Matching global mask to frame:\n"+" ".join(swarp_mask.split()))
                ret = subprocess.Popen(swarp_mask.split(), 
                                       stdout=subprocess.PIPE, 
                                       stderr=subprocess.PIPE)
                (swarp_stdout, swarp_stderr) = ret.communicate()
                if (sitesetup.log_shell_output):
                    logger.debug("\nCommand:\n%s\n--> Returncode: %d\n---\nStd.Out:\n%s\n---\nStd.Err:\n%s\n---", 
                                 swarp_cmd, ret.returncode, swarp_stdout, swarp_stderr)
                # if (len(swarp_stderr) > 0 and ret.returncode != 0):
                #     logger.warning("swarp stderr:\n"+swarp_stderr)
                # else:
                #     logger.debug("swarp stderr:\n"+swarp_stderr)
                end_time = time.time()
                logger.debug("Creating mask for X finished successfully after %.2d seconds" % (end_time-start_time))
            except:
                pass

            logger.info("Done with aligned mask: %s" % (mask_aligned))

            #
            # Multiply the weight mask of this single frame with the mask, 
            # thus eliminating all sources we want to mask out
            #
            logger.info("Applying mask to weightmap (%s)" % (single_file))
            mask_hdu = pyfits.open(mask_aligned)
            weightmap_file = single_file[:-5]+".weight.fits"
            weightmap_hdu = pyfits.open(weightmap_file, mode='update')

            weightmap_hdu[0].data[mask_hdu[0].data > 0] = 0.

            weightmap_hdu.flush()
            weightmap_hdu.close()

        hdulist.close()
        sgl_queue.task_done()


def create_mask(fitsfile, swarp_params):

    logger.info("Creating mask (from %s)" % (fitsfile))

    unique_singledir = swarp_params['unique_singledir']
    #
    # Extract only the first extension
    # 
    hdulist = pyfits.open(fitsfile)

    # Get the mid-stack MJD date to find the closest mask in time when 
    # multiple masks are given
    mjd = hdulist[0].header['MJD-OBS']
    if ('MJD-MID' in hdulist[0].header): mjd = hdulist[0].header['MJD-MID']

    _, fitsbase = os.path.split(os.path.abspath(fitsfile))
    image_only_fits = "%s/%s.primaryonly.fits" % (unique_singledir, fitsbase)
    pyfits.HDUList([hdulist[0]]).writeto(image_only_fits, clobber=True)

    #
    # We use source-extractor to create the mask
    #        
    sex_default = "%s/.config/swarpstack_mask.conf" % (sitesetup.exec_dir)
    params_default = "%s/.config/swarpstack_mask.params" % (sitesetup.exec_dir)
    segmentation_file = "%s/%s.segmentation.fits" % (unique_singledir, fitsbase)

    sex_cmd = """
    %(sex)s 
    -c %(config)s 
    -PARAMETERS_NAME %(params)s
    -DETECT_THRESH %(nsigma)f 
    -DETECT_MINAREA %(minarea)f
    -CHECKIMAGE_TYPE SEGMENTATION
    -CHECKIMAGE_NAME %(segmentation_file)s
    %(inputfits)s
    """ % {
        'sex': sitesetup.sextractor,
        'config': sex_default,
        'params': params_default,
        'nsigma': swarp_params['mask-nsigma'],
        'minarea': swarp_params['mask-npix'],
        'segmentation_file': segmentation_file,
        'inputfits': image_only_fits,
        #swarp_params['mask-fits'],
        }

    # print "\n"*5,sex_cmd,"\n"*5
    # print " ".join(sex_cmd.split())

    #
    # Run SourceExtractor to compute the segmentation map
    #
    try:
        start_time = time.time()
        logger.debug("Computing segmentation map:\n%s" % (" ".join(sex_cmd.split())))
        
        ret = subprocess.Popen(sex_cmd.split(), 
                               stdout=subprocess.PIPE, 
                               stderr=subprocess.PIPE) #, shell=True)
        (sex_stdout, sex_stderr) = ret.communicate()
        if (sitesetup.log_shell_output):
            logger.debug("\nCommand:\n%s\n--> Returncode: %d\n---\nStd.Out:\n%s\n---\nStd.Err:\n%s\n---", 
                         sex_cmd, ret.returncode, sex_stdout, sex_stderr)

        # logger.debug("sex stdout:\n"+sex_stdout)
        # if (len(sex_stderr) > 0 and ret.returncode != 0):
        #     logger.warning("sex stderr:\n"+sex_stderr)
        # else:
        #     logger.debug("sex stderr:\n"+sex_stderr)
        end_time = time.time()
        logger.info("SourceExtractor returned after %.3f seconds" % (end_time - start_time))
    except OSError as e:
        podi_logging.log_exception()
        print >>sys.stderr, "Execution failed:", e

    #
    # Now convert the segmentation mask into a object mask
    # 
    seghdu = pyfits.open(segmentation_file)
    weight = numpy.zeros(shape=seghdu[0].data.shape, dtype=numpy.float32)
    weight[seghdu[0].data > 0] = 1.

    maskhdu = pyfits.HDUList([pyfits.PrimaryHDU(data=weight, header=seghdu[0].header.copy())])
    maskfile = "%s/%s.mask.fits" % (unique_singledir, fitsbase)
    clobberfile(maskfile)
    maskhdu.writeto(maskfile, clobber=True)

    seghdu.close()
    maskhdu.close()
    del seghdu
    del maskhdu

    return maskfile, mjd


def swarpstack(outputfile, 
               inputlist, 
               swarp_params, 
               options, 
               keep_intermediates=False, 
               unique_dir=None,
               ):

    logger = logging.getLogger("SwarpStack - Prepare")

    # Figure out the config path
    swarp_default = "%s/.config/swarp.default" % (sitesetup.exec_dir)
    logger.debug("Using swarp-default in %s" % (swarp_default))

    if (len(inputlist) <= 0):
        logger.error("No (valid) input files specified!")
        return

    master_reduction_files_used = {}
    
    ############################################################################
    #
    # Construct a unique name to hold intermediate files
    #
    ############################################################################
    if (not unique_dir == None and os.path.isdir(unique_dir)):
        unique_singledir = unique_dir
        keep_intermediates = True
    else:
        process_id = os.getpid()
        hostname = socket.gethostname()
        # Create a temporary directory
        unique_singledir = tempfile.mkdtemp(dir=sitesetup.swarp_singledir,
                                            prefix="%s-%05d----" % (hostname, process_id))
    # Save the directory name in swarp_params
    logger.info("Storing intermediate files in %s ..." % (unique_singledir))
    swarp_params['unique_singledir'] = unique_singledir


    ############################################################################
    #
    # Prepare the mask file(s) if requested
    #
    ############################################################################
    if (not swarp_params['mask-fits'] == None and
        swarp_params['mask'] == None):

        mask_list = []
        mask_mjds = []
        for mf in swarp_params['mask-fits']:
            maskfile, mjd = create_mask(mf, swarp_params)
            mask_list.append(maskfile)
            mask_mjds.append(mjd)

        swarp_params['mask-list'] = mask_list
        swarp_params['mask-mjds'] = numpy.array(mask_mjds)
            
        # swarp_params['mask'] = maskfile

    logger.info("Removing some OTAs from input: %s" % (str(options['skip_otas'])))

    ############################################################################
    #
    # Generate the illumination correction file if this is requested
    #
    ############################################################################
    if (swarp_params['illumcorr'] == "autogenerate" and
        swarp_params['illumcorrfiles'] != None):
        
        # Make sure all files exist
        ic_filelist = []
        for fn in swarp_params['illumcorrfiles'].split(","):
            if (os.path.isfile(fn)):
                ic_filelist.append(fn)
        
        logger.debug("Using the following files to create an illumination correction:\n -- %s" % (
            "\n -- ".join(ic_filelist)))

        ic_filename = "%s.illumcorr.fits" % (outputfile[:-5])
        podi_illumcorr.prepare_illumination_correction(
            filelist=ic_filelist,
            outfile=ic_filename,
            tmpdir=unique_singledir,
            redo=True)

        # Change the internal parameter to use the newly generated
        # illumination correction file during stacking
        params['illumcorr'] = ic_filename
        options['illumcorr_dir'] = ic_filename

    ###########################################################################
    #
    # Handle the optional user-requested zeropoint determination based on the 
    # -best or median options
    #
    ###########################################################################
    if (swarp_params['target_magzero'] in ['best', 'median']):
        # Load all frames, and get a list of all available magzero headers
        all_zp = numpy.empty((len(inputlist)))
        all_zp[:] = numpy.NaN
        for idx, fn in enumerate(inputlist):
            if (os.path.isfile(fn)):
                _hdu = pyfits.open(fn)
                magzero = _hdu[0].header['PHOTZP_X'] if 'PHOTZP_X' in _hdu[0].header else numpy.NaN
                logger.debug("ZP (%s) = %.4f" % (fn, magzero))
                all_zp[idx] = magzero
        logger.info(str(all_zp))
        all_zp = all_zp[numpy.isfinite(all_zp)]
        if (all_zp.shape[0] == 0):
            final_magzero = 25.
            logger.warning("Didn't find any valid zeropoints")
        elif (swarp_params['target_magzero'] == "best"):
            final_magzero = numpy.max(all_zp)
            logger.debug("Selecting ''best'' phot. ZP of %.3f" % (final_magzero))
        elif (swarp_params['target_magzero'] == "median"):
            final_magzero = numpy.median(all_zp)
            logger.debug("Selecting ''median'' phot. ZP of %.3f" % (final_magzero))
        else:
            logger.error("Problem determing which phot. ZP to use")
            final_magzero = 25.0
        swarp_params['target_magzero'] = final_magzero
    elif (type(swarp_params['target_magzero']) == str and
          os.path.isfile(swarp_params['target_magzero'])):
        _hdu = pyfits.open(swarp_params['target_magzero'])
        magzero = _hdu[0].header['PHOTZP_X'] if 'PHOTZP_X' in _hdu[0].header else 25.0
        logger.debug("Matching ZP to %s ==> ZP = %.4f" % (swarp_params['target_magzero'], magzero))
        swarp_params['target_magzero'] = magzero
    else:
        try:
            swarp_params['target_magzero'] = float(swarp_params['target_magzero'])
        except:
            logger.error("Invalid parameter or file not found: %s" % (str(swarp_params['target_magzero'])))
            swarp_params['target_magzero'] = 25.0
    logger.info("Scaling all frames to common phot. ZP of %.4f" % (swarp_params['target_magzero']))


    ############################################################################
    #
    # Prepare all QR'ed input files, applying additional corrections where needed
    #
    ############################################################################

    stack_start_time = 1e9
    stack_end_time = -1e9
    stack_total_exptime = 0
    stack_framecount = 0

    # print "input=",inputlist
    # print "output=",outputfile

    modified_files, stack_total_exptime, stack_framecount, \
        stack_start_time, stack_end_time, master_reduction_files_used, \
        nonsidereal_offsets, photom_lists = \
        prepare_input(inputlist, swarp_params, options)

    # print modified_files
    inputlist = modified_files

    # print "\n\n".join(inputlist)

    #print photom_lists
    gain_list, skylevel_list, weight_list = photom_lists


    add_only = swarp_params['add'] and os.path.isfile(outputfile)
    if (add_only):
        logger.info("Activating ADD mode")

    if (outputfile.endswith(".fits")):
        outputfile = outputfile[:-5]

    header_only_file = "%s/preswarp.fits" % (unique_singledir)
    logger.debug("Using header-only-file: %s" % (header_only_file))

    # Make sure the reference file is a valid file
    if (not swarp_params['reference_file'] == None):
        if (os.path.isfile(swarp_params['reference_file'])):
            logger.info("Using %s as reference file" % (swarp_params['reference_file']))
        else:
            logger.error("Could not find specified reference file (%s)" % (swarp_params['reference_file']))
            swarp_params['reference_file'] = None

    logging.debug("Using modified input list: %s" % (str(inputlist)))

    ############################################################################
    #
    # Figure out the pixel-grid and sky-coverage of the final stack 
    #
    ############################################################################
    logger.info("reference_file = %s" % (str(swarp_params['reference_file'])))
    if (add_only or not swarp_params['reference_file'] == None):
        #
        # This is the simpler add-only mode
        #

        logger.info("Reading sky-coverage from reference frame")

        if (not swarp_params['reference_file'] == None):
            output_info = pyfits.open(swarp_params['reference_file'])
        else:
            # Open the existing output header and get data from there
            output_info = pyfits.open(outputfile+".fits")

        logger.info("Stack information...")
        logger.info("   Output-dimensions: %(NAXIS1)5d x %(NAXIS2)5d" % (output_info[0].header))
        logger.info("       Output center: %(CRVAL1)10.6f / %(CRVAL2)10.6f" % (output_info[0].header))
        out_crval1 = output_info[0].header['CRVAL1']
        out_crval2 = output_info[0].header['CRVAL2']
        out_naxis1 = output_info[0].header['NAXIS1']
        out_naxis2 = output_info[0].header['NAXIS2']

        output_info.close()

        if (swarp_params['pixelscale'] <= 0):
            swarp_params['pixelscale'] = math.fabs(output_info[0].header['CD1_1']) * 3600.
            logger.info("Computing pixelscale from data: %.4f arcsec/pixel" % (swarp_params['pixelscale']))
    else:
        #
        # This is the regular start-from-scratch mode
        #

        # Set some Swarp options
        swarp_opts = """ \
               -IMAGEOUT_NAME %(imageout)s \
               -WEIGHTOUT_NAME %(weightout)s \
               -COMBINE_TYPE %(combine_type)s \
              """ % {
                  'imageout': header_only_file,
                  'weightout': "%s/preswarp.weight.fits" % (unique_singledir),
                  'combine_type': 'AVERAGE',
              }

        if (swarp_params['pixelscale'] > 0):
            swarp_opts += " -PIXELSCALE_TYPE MANUAL -PIXEL_SCALE %.4f " % (swarp_params['pixelscale'])

            #
            # Factor in the potential change in pixelscale in the skylevel and 
            # gain computations
            #
            pixelscale_raw = 0.11
            pixelscale_binning = (swarp_params['pixelscale'] / pixelscale_raw)**2
            # do not multiply gain with binning, since flux/pixel is summed, not averages
            skylevel_list *= pixelscale_binning
            logger.info("Adjusting gain and skylevel for change in pixelscale (%.2f''/px --> bin=%.2f)" % (
                swarp_params['pixelscale'], pixelscale_binning))
        else:
            logger.info("Pixelscale remains unchanged, no need to adjust gain or skylevel")


        if (swarp_params['no-fluxscale']):
            swarp_opts += " -FSCALE_KEYWORD none "

        swarp_opts += " -SUBTRACT_BACK %s " % ("Y" if swarp_params['subtract_back']=='swarp' else "N")

        logger.debug("SWARP options for pre-stack:\n"+" ".join(swarp_opts.split()))


        # 
        # First create only the output header so we can pass some information 
        # to the user
        #
        swarp_cmd = "%(swarp)s %(opts)s -HEADER_ONLY Y %(files)s" % {
            'swarp': sitesetup.swarp_exec,
            'opts': swarp_opts,
            'files': " ".join(inputlist),
        }
        logger.debug("swarp_cmd=\n"+swarp_cmd)


        try:
            logger.info("Computing sky-coverage ...")
            logger.debug("Computing preswarp:\n%s" % (" ".join(swarp_cmd.split())))
            ret = subprocess.Popen(swarp_cmd.split(), 
                                   stdout=subprocess.PIPE, 
                                   stderr=subprocess.PIPE) #, shell=True)
            # if retcode < 0:
            #     print >>sys.stderr, "Child was terminated by signal", -retcode
            # else:
            #     print >>sys.stderr, "Child returned", retcode
            #print retcode.stdout.readlines()
            #print retcode.stderr.readlines()
            (swarp_stdout, swarp_stderr) = ret.communicate()
            if (sitesetup.log_shell_output):
                logger.debug("\nCommand:\n%s\n--> Returncode: %d\n---\nStd.Out:\n%s\n---\nStd.Err:\n%s\n---", 
                             swarp_cmd, ret.returncode, swarp_stdout, swarp_stderr)

            # logger.debug("swarp stdout:\n"+swarp_stdout)
            # if (len(swarp_stderr) > 0 and ret.returncode != 0):
            #     logger.warning("swarp stderr:\n"+swarp_stderr)
            # else:
            #     logger.debug("swarp stderr:\n"+swarp_stderr)
        except OSError as e:
            podi_logging.log_exception()
            print >>sys.stderr, "Execution failed:", e

        #
        # some information about the resulting stack is in the output-file
        #

        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                output_info = pyfits.open(header_only_file)
        except IOError:
            podi_logging.log_exception()
            logger.error("Couldn't open the pre-swarp file, aborting")
            return
            
        logger.info("Stack information...")
        logger.info("   Output-dimensions: %(NAXIS1)5d x %(NAXIS2)5d" % (output_info[0].header))

        out_crval1 = output_info[0].header['CRVAL1']
        out_crval2 = output_info[0].header['CRVAL2']
        out_naxis1 = output_info[0].header['NAXIS1']
        out_naxis2 = output_info[0].header['NAXIS2']

        output_info.close()

        outimage_npixels = (out_naxis1 * out_naxis2) 
        if (outimage_npixels > 1e9):
            outimage_npixels /= 1e9
            if (swarp_params['huge_frame_allowed']):
                logger.warning("The output file is going to be huge (%.3f GigaPixels), continuing" % (outimage_npixels))
            else:
                logger.error("The output file exceeds the maximum allowed image size (%.3f GigaPixels)" % (outimage_npixels))
                cleanup_singles(swarp_params['unique_singledir'], logger)
                return None
        
        if (swarp_params['pixelscale'] <= 0):
            swarp_params['pixelscale'] = math.fabs(output_info[0].header['CD1_1']) * 3600.
            #pixelscale = (output_info[0].header['CD1_1'] * output_info[0].header['CD2_2'] \
            #             - output_info[0].header['CD1_2'] * output_info[0].header['CD2_1']) * 3600.
            logger.info("Computing pixelscale from data: %.4f arcsec/pixel" % (swarp_params['pixelscale']))
    
    #############################################################################
    #
    # Prepare the individual frames, rectified and re-projected 
    # to the final grid
    #
    #############################################################################
    logger = logging.getLogger("SwarpStack - Singles")
    single_prepared_files = []

    # Prepare the worker queue
    sgl_queue = multiprocessing.JoinableQueue()

    # print inputlist
    # sys.exit(0)

    logger.info("Preparing focal-plane frames for %d frames..." % (len(inputlist)))
    for i in range(len(inputlist)):
        prepared_file = inputlist[i]
        # for prepared_file in inputlist:
        nonsidereal_dradec = nonsidereal_offsets[i]
        # print prepared_file, nonsidereal_dradec

        hdulist = pyfits.open(prepared_file)
        obsid = hdulist[0].header['OBSID']
        hdulist.close()

        fluxscale_kw = 'XXXXXXXX'
        magzero = hdulist[0].header['PHOTZP_X'] if 'PHOTZP_X' in hdulist[0].header else -99.
        # print magzero, swarp_params['no-fluxscale']
        # if (magzero > 0 and not swarp_params['no-fluxscale']):
        #     fluxscale_value = math.pow(10, 0.4*(swarp_params['target_magzero']-magzero))
        # else:
        #     exptime = hdulist[0].header['EXPMEAS'] if 'EXPMEAS' in hdulist[0].header else (
        #         hdulist[0].header['EXPTIME'] if 'EXPTIME' in hdulist[0].header else 1.0)
        #     fluxscale_value = 1./exptime

        # assemble all swarp options for that run
        dic = {'singledir': unique_singledir, #sitesetup.swarp_singledir,
               'obsid': obsid,
               'pixelscale': swarp_params['pixelscale'],
               'pixelscale_type': "MANUAL" if swarp_params['pixelscale'] > 0 else "MEDIAN",
               'center_ra': out_crval1,
               'center_dec': out_crval2,
               'imgsizex': out_naxis1,
               'imgsizey': out_naxis2,
               'resample_dir': unique_singledir,
               'inputfile': prepared_file,
               'swarp_default': swarp_default,
               'fluxscale_kw': fluxscale_kw, #'none' if swarp_params['no-fluxscale'] else 'FLXSCALE'
               'fluxscale_value': 1.0, #fluxscale_value,
               'fileid': i+1,
               'delete_tmpfiles': "N" if keep_intermediates else "Y",
           }

        single_file = "%(singledir)s/%(obsid)s.%(fileid)d.fits" % dic

        swarp_opts = """
                 -c %(swarp_default)s 
                 -IMAGEOUT_NAME %(singledir)s/%(obsid)s.%(fileid)d.fits 
                 -WEIGHTOUT_NAME %(singledir)s/%(obsid)s.%(fileid)d.weight.fits 
                 -PIXEL_SCALE %(pixelscale)f 
                 -PIXELSCALE_TYPE %(pixelscale_type)s 
                 -COMBINE Y 
                 -COMBINE_TYPE WEIGHTED
                 -CENTER_TYPE MANUAL 
                 -CENTER %(center_ra)f,%(center_dec)f 
                 -IMAGE_SIZE %(imgsizex)d,%(imgsizey)d 
                 -RESAMPLE_DIR %(resample_dir)s 
                 -SUBTRACT_BACK N 
                 -FSCALE_KEYWORD %(fluxscale_kw)s 
                 -FSCALE_DEFAULT %(fluxscale_value).10e 
                 -WEIGHT_TYPE MAP_WEIGHT
                 -WEIGHT_SUFFIX .weight.fits 
                 -RESCALE_WEIGHTS N
                 -DELETE_TMPFILES %(delete_tmpfiles)s \
                 %(inputfile)s 
                 """ % dic

#                 -WEIGHT_TYPE MAP_WEIGHT 

#                 -WEIGHT_THRESH 5

        # print swarp_opts
        swarp_cmd = "%s %s" % (sitesetup.swarp_exec, swarp_opts)

        if (add_only and os.path.isfile(single_file)):
            logger.info("This single-swarped file (%s) exist, skipping it" % (single_file))
        elif (swarp_params['reuse_singles'] and os.path.isfile(single_file)):
            logger.info("This single-swarped file (%s) exist, re-using it" % (single_file))
            single_prepared_files.append(single_file)
        else:
            logger.debug("Queuing single file %s:\n%s" % (
                prepared_file, " ".join(swarp_cmd.split()))
            )

            # print (swarp_cmd, prepared_file, single_file, swarp_params, nonsidereal_dradec, True)

            sgl_queue.put( (swarp_cmd, prepared_file, single_file, swarp_params, nonsidereal_dradec, True) )
            single_prepared_files.append(single_file)
            # time.sleep(2)

    #
    # Execute all swarps to create the single files
    #

    # Now with all swarp-runs queued, start a number of processes
    worker_args = (sgl_queue, "")
    processes = []
    for i in range(sitesetup.number_cpus):
        p = multiprocessing.Process(target=mp_swarp_single, args=worker_args)
        p.start()
        processes.append(p)

        # also add a quit-command for each process
        sgl_queue.put(None)
        
    # wait until all work is done
    sgl_queue.join()
    # join/terminate all processes
    for p in processes:
        p.join()

    #
    # If in "add" mode, rename the previous output file and add it to the list of input files
    #
    if (add_only):

        if (len(single_prepared_files) < 1):
            logger.info("No new files were added, so there's nothing to do.")
            return

        prev = 1
        while (True):
            filename = "%s.prev%02d.fits" % (outputfile, prev)
            if (not os.path.isfile(filename)):
                break
            prev += 1
            continue
                
        # Rename the current output file and its weights
        old_stacked = "%s.prev%02d.fits" % (outputfile, prev)
        old_weight = "%s.prev%02d.weight.fits" % (outputfile, prev)

        os.rename(outputfile+".fits", old_stacked)
        logger.debug("renamed old stack %s -> %s" % (outputfile+".fits", old_stacked))

        os.rename(outputfile+".weight.fits", old_weight)
        logger.debug("renamed old stack weight %s -> %s" % (outputfile+".weight.fits", old_weight))

        # Also add the new re-named old stacked file to list of input files
        single_prepared_files.append(old_stacked)
        logger.debug("Adding now old stack file to input list")
    #
    # Done re-naming the old file
    #

    # sys.exit(0)

    #############################################################################
    #
    # Now perform the background subtraction on each of the prepared single files
    # The resulting output files can then be used for the final stack without
    # additional resampling
    #
    #############################################################################

    #
    # Now use some brains to figure out the best way of setting the background 
    # subtraction to get s nice smooth background that does not over-subtract 
    # the target.
    #
    bg_opts = ""
    if (swarp_params['target_dimension'] > 0 and swarp_params['subtract_back']=='swarp'):
        dic['BACK_TYPE'] = "AUTO"
        dic['BACK_SIZE'] = 128
        dic['BACK_FILTERSIZE'] = 3

        # Rule of thum: larger objects: make both filtersize and back_size 
        # larger
        # first compute a reference size for the default settings
        ref_size = dic['BACK_SIZE'] * dic['BACK_FILTERSIZE'] \
                   * swarp_params['pixelscale'] / 60. \
                   * 0.1  # the last factor is a fudge-factor
        logger.debug("Reference size: %f" % (ref_size))
        # Now scale up the filtersize, making sure it stays between 3 and 7
        filtersize = int(math.floor(math.sqrt(swarp_params['target_dimension'] / ref_size) * dic['BACK_FILTERSIZE']))
        logger.debug("Simple filter size: %d" % (filtersize))
        if (filtersize < 3): filtersize = 3
        if (filtersize > 7): filtersize = 7

        # in a next step, modify the backsize parameter. Make sure it does not
        # become too large or too small
        backsize = (swarp_params['target_dimension'] * 60. / swarp_params['pixelscale']) / filtersize

        logger.debug("BACK-SIZE: %f" % (backsize))
        if (backsize < 64): backsize = 64
        if (backsize > 600): backsize = 600

        dic['BACK_SIZE'] = backsize
        dic['BACK_FILTERSIZE'] = filtersize

        bg_opts = """  
               -BACK_TYPE %(BACK_TYPE)s
               -BACK_SIZE %(BACK_SIZE)d
               -BACK_FILTERSIZE %(BACK_FILTERSIZE)d
        """ % dic
        logger.debug("Adding background parameters:\n\n"+bg_opts+"\n\n")
        # swarp_opts += bg_opts


    if (swarp_params['subtract_back']=='swarp'):
        logger = logging.getLogger("SwarpStack - SkySub")
        logger.info("Performing sky-subtraction on all frames")

        final_prepared_files = []

        # Prepare the worker queue
        sgl_queue = multiprocessing.JoinableQueue()

        fileid = 0
        for prepared_file in single_prepared_files:
            hdulist = pyfits.open(prepared_file)
            obsid = hdulist[0].header['OBSID']
            hdulist.close()
            fileid += 1

            # assemble all swarp options for that run
            bgsub_file = "%(singledir)s/%(obsid)s.%(fileid)d.bgsub.fits" % {
                'singledir': unique_singledir,
                'obsid': obsid,
                'fileid': fileid,}
            bgsub_weight_file = "%(singledir)s/%(obsid)s.%(fileid)d.bgsub.weight.fits" % {
                'singledir': unique_singledir,
                'obsid': obsid,
                'fileid': fileid,}

            dic = {'singledir': unique_singledir,
                   'obsid': obsid,
                   'pixelscale': swarp_params['pixelscale'],
                   'pixelscale_type': "MANUAL" if swarp_params['pixelscale'] > 0 else "MEDIAN",
                   'center_ra': out_crval1,
                   'center_dec': out_crval2,
                   'imgsizex': out_naxis1,
                   'imgsizey': out_naxis2,
                   'resample_dir': unique_singledir,
                   'inputfile': prepared_file,
                   'swarp_default': swarp_default,
                   'bgsub': "Y" if swarp_params['subtract_back']=='swarp' else "N",
                   'bgsub_file': bgsub_file,
                   'bgsub_weight_file': bgsub_weight_file,
                   'bgopts': bg_opts,
                   'inputfile': prepared_file,
                   'fileid': fileid,
                   'delete_tmpfiles': "N" if keep_intermediates else "Y",
               }
            
            swarp_opts = """\
                     -c %(swarp_default)s \
                     -IMAGEOUT_NAME %(bgsub_file)s \
                     -WEIGHTOUT_NAME %(bgsub_weight_file)s \
                     -PIXEL_SCALE %(pixelscale)f \
                     -PIXELSCALE_TYPE %(pixelscale_type)s \
                     -COMBINE Y \
                     -COMBINE_TYPE WEIGHTED \
                     -CENTER_TYPE MANUAL \
                     -CENTER %(center_ra)f,%(center_dec)f \
                     -IMAGE_SIZE %(imgsizex)d,%(imgsizey)d \
                     -RESAMPLE_DIR %(resample_dir)s \
                     -RESAMPLE Y \
                     -SUBTRACT_BACK %(bgsub)s \
                     -WEIGHT_TYPE MAP_WEIGHT \
                     -WEIGHT_SUFFIX .weight.fits \
                     -RESCALE_WEIGHTS N \
                     -DELETE_TMPFILES %(delete_tmpfiles)s \
                     -WRITE_FILEINFO Y
                     -FSCALE_KEYWORD XXXXXXXX \
                     -FSCALE_DEFAULT 1.0 \
                     %(bgopts)s \
                     %(inputfile)s \
                     """ % dic

            
            # print swarp_opts
            swarp_cmd = "%s %s" % (sitesetup.swarp_exec, swarp_opts)
            # print swarp_cmd

            # Disable the mask, since we already prepared it earlier
            swarp_params['mask'] = None

            if (add_only and os.path.isfile(bgsub_file)):
                logger.info("This single-swarped file (%s) exist, skipping it" % (bgsub_file))
            elif (swarp_params['reuse_singles'] and os.path.isfile(bgsub_file)):
                logger.info("This single-swarped file (%s) exist, re-using it" % (bgsub_file))
                final_prepared_files.append(bgsub_file)
            else:
                logger.info("Preparing file %s, please wait ..." % (prepared_file))
                logger.debug(" ".join(swarp_cmd.split()))

                sgl_queue.put( (swarp_cmd, prepared_file, bgsub_file, swarp_params, None, False) )
                final_prepared_files.append(bgsub_file)

        #
        # Execute all swarps to create the single files
        #

        # Now with all swarp-runs queued, start a number of processes
        worker_args = (sgl_queue, "")
        processes = []
        for i in range(sitesetup.number_cpus):
            p = multiprocessing.Process(target=mp_swarp_single, args=worker_args)
            p.start()
            processes.append(p)

            # also add a quit-command for each process
            sgl_queue.put(None)

        # wait until all work is done
        sgl_queue.join()
        # join/terminate all processes
        for p in processes:
            p.join()

    else:
        # No background subtraction was requested
        final_prepared_files = single_prepared_files

    logging.debug("files to stack: %s" % (str(final_prepared_files)))

    # sys.exit(0)

    #############################################################################
    #
    # Now all single files are prepared, go ahead and produce the actual stack
    # Use the background-subtracted or single files from above as direct input, 
    # i.e. do not resample these files as they are already on the right pixel grid.
    #
    #############################################################################
    for combine_type in swarp_params['combine-type']:
        dic['combine_type'] = combine_type #swarp_params['combine-type'] #"AVERAGE"
        dic['imageout'] = "%s.%s.fits" % (outputfile, combine_type)
        dic['weightout'] = "%s.%s.weight.fits" % (outputfile, combine_type) #outputfile+".weight.fits"
        dic['prepared_files'] = " ".join(single_prepared_files)
        dic['bgsub'] = "N" # as this was done before if swarp_params['subtract_back'] else "N"
        dic['clip-sigma'] = swarp_params['clip-sigma']
        dic['clip-ampfrac'] = swarp_params['clip-ampfrac']

        swarp_opts = """
                     -c %(swarp_default)s 
                     -IMAGEOUT_NAME %(imageout)s 
                     -WEIGHTOUT_NAME %(weightout)s 
                     -COMBINE_TYPE %(combine_type)s 
                     -PIXEL_SCALE %(pixelscale)f 
                     -PIXELSCALE_TYPE %(pixelscale_type)s 
                     -COMBINE Y 
                     -COMBINE_TYPE %(combine_type)s 
                     -CLIP_AMPFRAC %(clip-ampfrac)f 
                     -CLIP_SIGMA %(clip-sigma)f 
                     -CENTER_TYPE MANUAL 
                     -CENTER %(center_ra)f,%(center_dec)f 
                     -IMAGE_SIZE %(imgsizex)d,%(imgsizey)d 
                     -RESAMPLE N 
                     -RESAMPLE_DIR %(singledir)s 
                     -SUBTRACT_BACK %(bgsub)s 
                     -WEIGHT_TYPE MAP_WEIGHT 
                     -WEIGHT_SUFFIX .weight.fits 
                     -RESCALE_WEIGHTS N 
                     -DELETE_TMPFILES N 
                     -WRITE_FILEINFO Y
                     """ % dic

        logger = logging.getLogger("SwarpStack - FinalStack")
        logger.info("Starting final stacking (%s) ..." % (combine_type))
        # print swarp_opts

        swarp_cmd = "%s %s %s" % (sitesetup.swarp_exec, swarp_opts, " ".join(final_prepared_files))
        logger.debug("swarp-options:%s"  %(swarp_cmd))
        logger.debug("\n"+" ".join(swarp_cmd.split()))
        try:
            ret = subprocess.Popen(swarp_cmd.split(), 
                                       stdout=subprocess.PIPE, 
                                       stderr=subprocess.PIPE)
            (swarp_stdout, swarp_stderr) = ret.communicate()
            if (sitesetup.log_shell_output):
                logger.debug("\nCommand:\n%s\n--> Returncode: %d\n---\nStd.Out:\n%s\n---\nStd.Err:\n%s\n---", 
                             swarp_cmd, ret.returncode, swarp_stdout, swarp_stderr)

            # logger.debug("swarp stdout:\n"+swarp_stdout)
            # if (len(swarp_stderr) > 0 and ret.returncode != 0):
            #     logger.warning("swarp stderr:\n"+swarp_stderr)
            # else:
            #     logger.debug("swarp stderr:\n"+swarp_stderr)
            #print "\n".join(swarp_stderr)
            logger.info("done, swarp returned (ret-code: %d)!" % ret.returncode)
        except OSError as e:
            podi_logging.log_exception()
            print >>sys.stderr, "Execution failed:", e

        logger.info("Stack (%s) complete, adding headers" % (dic['imageout']))

        # Finally, open the output file and copy a bunch of headers into it
        hdustack = pyfits.open(dic['imageout'], mode='update')
        # Also open the first frame in the stack to be used as data source
        firsthdu = pyfits.open(inputlist[0])

        if ('FILE0001' in hdustack[0].header):
            add_fits_header_title(hdustack[0].header, "Input files as written by swarp", 'FILE0001')

        for hdrkey in [
                'TARGRA', 'TARGDEC',
                'FILTER', 'FILTERID', 'FILTDSCR', 
                'OBSID', 'OBJECT', 
                'DATE-OBS', 'TIME-OBS', 'MJD-OBS']:
            if (hdrkey in firsthdu[0].header):
                key, val, com = firsthdu[0].header.cards[hdrkey]
                hdustack[0].header[key] = (val, com)
        add_fits_header_title(hdustack[0].header, "Headers inherited from input frames", 'TARGRA')

        del hdustack[0].header['EXPTIME']
        hdustack[0].header['EXPTIME'] = (stack_total_exptime, "total exposure time in stack")
        hdustack[0].header['MJD-STRT'] = (stack_start_time, "MJD at start of earliest exposure")
        hdustack[0].header['MJD-END'] = (stack_end_time, "MJD at end of last exposure")
        hdustack[0].header['NCOMBINE'] = (stack_framecount, "number of exposures in stack")
        add_fits_header_title(hdustack[0].header, "Computed timing information", 'EXPTIME')

        # Add some additional headers
        hdustack[0].header['MAGZERO']  = (swarp_params['target_magzero'],
                                          "after flux-scaling each input exposure")
        hdustack[0].header['BACKGSUB'] = (swarp_params['subtract_back'] if swarp_params['subtract_back'] else "none",
                                          "was background subtracted?")
        hdustack[0].header['PIXLSCAL'] = (swarp_params['pixelscale'],
                                          "user-selected pixelscale")
        hdustack[0].header['REUSESGL'] = ("yes" if swarp_params['reuse_singles'] else "no",
                                          "reuse singles?")

        #
        #
        # Add all photometry-relevant derived keywords in the output stack
        #
        #
        if (combine_type in ['SUM']):
            stack_gain = numpy.mean(gain_list)
            stack_skylevel = numpy.sum(skylevel_list)
        else:
            stack_gain = numpy.sum(gain_list)
            if (combine_type in ['WEIGHTED']):
                # compute weighted sky-level
                stack_skylevel = numpy.sum(skylevel_list*weight_list) / numpy.sum(weight_list)
            else:
                stack_skylevel = numpy.mean(skylevel_list)
        for hk in ['GAIN', 'SKYLEVEL']:
            if (hk in hdustack[0].header):
                del hdustack[0].header[hk]
        hdustack[0].header['GAIN'] = (stack_gain, "combined GAIN")
        hdustack[0].header['SKYLEVEL'] = (stack_skylevel, "combined skylevel")
        logger.info("Stack: GAIN=%f - SKY=%f" % (stack_gain, stack_skylevel))

        # Check if we are to add the sky-level back into the stack
        hdustack[0].header['NORMSKY'] = (False, "re-normalize sky")
        if (params['normalize_sky'] and not params['subtract_back'] == False):
            logger.info("Adding back skylevel (%f) to stack" % (stack_skylevel))
            hdustack[0].data += stack_skylevel
            hdustack[0].header['NORMSKY'] = True
            
        #
        # Store all configuration about non-sidereal / ephemerides corrections 
        # that were applied during execution
        #
        nonsid_mode = 'none'
        if (swarp_params['use_nonsidereal']): nonsid_mode = 'nonsidereal'
        if ('ephemerides' in swarp_params and not swarp_params['ephemerides'] == None): nonsid_mode = "ephemerides"
        hdustack[0].header['NONSIDRL'] = (nonsid_mode,
                                          "Non-sidereal correction")
        valid_nonsidereal_reference = None
        try:
            hdustack[0].header['NSID_RA']  = (options['nonsidereal']['dra'],
                                              "non-sidereal rate dRA*cosDec [arcsec/hr]")
            hdustack[0].header['NSID_DEC'] = (options['nonsidereal']['ddec'],
                                              "non-sidereal rate dDec [arcsec/hr]")
            hdustack[0].header['NSID_RT']  = (options['nonsidereal']['ref_mjd'],
                                              "non-sidereal reference MJD")
            hdustack[0].header['NSID_RF']  = (options['nonsidereal']['ref'],
                                              "non-sidereal ref. from cmd line")

            hdustack[0].header['NSREFMJD'] = options['nonsidereal']['ref_mjd']
            hdustack[0].header['NSREFILE'] = os.path.abspath(options['nonsidereal']['ref'])
            hdustack[0].header['NSREFOBS'] = options['nonsidereal']['ref_obsid']

            if (os.path.isfile(options['nonsidereal']['ref'])):
                valid_nonsidereal_reference = options['nonsidereal']['ref']
        except:
            pass

        #
        # Check if we use the ephem mode; if so add some more headers
        #
        try:
            eph = swarp_params['ephemerides']
            hdustack[0].header['EPHM-MJD'] = eph['ref-mjd']
            hdustack[0].header['EPHM-REF'] = eph['ref']
            hdustack[0].header['EPHMMODE'] = eph['mode']
            hdustack[0].header['EPHMTRGT'] = eph['target']
            hdustack[0].header['EPHMFILE'] = eph['datafile']
            hdustack[0].header['NSREFMJD'] = eph['ref-mjd']
            hdustack[0].header['NSREFILE'] = os.path.abspath(eph['ref'])
            hdustack[0].header['NSREFOBS'] = eph['ref-obsid']

            if (os.path.isfile(eph['ref'])):
                valid_nonsidereal_reference = eph['ref']
        except:
            pass
            
        add_fits_header_title(hdustack[0].header, "Non-sidereal/ephemerides configuration", 'NONSIDRL')


        firsthdu.close()
        add_fits_header_title(hdustack[0].header, "SwarpStack parameters supplied by user", 'MAGZERO')

        if (not valid_nonsidereal_reference == None):
            master_reduction_files_used = \
                collect_reduction_files_used(master_reduction_files_used, 
                                             {"mjd-reference": valid_nonsidereal_reference})
            firsthdu = pyfits.open(valid_nonsidereal_reference)
            try:
                hdustack[0].header['NSR-OBST'] = (firsthdu[0].header['TIME-OBS'], 
                                                  "reference TIME-OBS")
                hdustack[0].header['NSR-OBSD'] = (firsthdu[0].header['DATE-OBS'], 
                                                  "reference DATE-OBS")
                hdustack[0].header['NSR-OBSM'] = (firsthdu[0].header['MJD-OBS'], 
                                                  "reference MJD-OBS")

                hdustack[0].header['NSR-MIDT'] = (firsthdu[0].header['TIME-MID'], 
                                                  "reference TIME-MID")
                hdustack[0].header['NSR-MIDD'] = (firsthdu[0].header['DATE-MID'], 
                                                  "reference DATE-MID")
                hdustack[0].header['NSR-MIDM'] = (firsthdu[0].header['MJD-MID'], 
                                                  "reference MJD-MID")

                hdustack[0].header['NSR-ENDT'] = (firsthdu[0].header['TIME-END'], 
                                                  "reference TIME-END")
                hdustack[0].header['NSR-ENDD'] = (firsthdu[0].header['DATE-END'], 
                                                  "reference DATE-END")
                hdustack[0].header['NSR-ENDM'] = (firsthdu[0].header['MJD-END'], 
                                                  "reference MJD-END")
                add_fits_header_title(hdustack[0].header, 
                                      "Timing information from reference frame", 'NSR-OBST')
            except:
                pass
            firsthdu.close()

        # Add the user-defined keywords to the stacked file. This is required for
        # proper integration with the PPA framework.
        # print options['additional_fits_headers']
        first_useradded_key = None
        for key, value in options['additional_fits_headers'].iteritems():
            if (first_useradded_key == None): first_useradded_key = key
            hdustack[0].header[key] = (value, "user-added keyword")
        if (len(options['additional_fits_headers']) > 0 and not first_useradded_key == None):
            add_fits_header_title(hdustack[0].header, 
                                  "user-added keywords", first_useradded_key)

        #
        # Create an association table from the master reduction files used.
        # 
        # print master_reduction_files_used
        assoc_table = create_association_table(master_reduction_files_used)
        hdustack.append(assoc_table)

        hdustack.flush()
        hdustack.close()
    
    # 
    # Delete all temporary files and the temp-directory
    # 
    if (not keep_intermediates):
        logger.info("Deleting all intermediate files")
        try:
            shutil.rmtree(unique_singledir)
        except:
            logger.error("There was a problem with recursively deleting the temp directory")
            podi_logging.log_exception()
            pass
 
    logger.info("All done!")

    return modified_files, single_prepared_files, final_prepared_files, unique_singledir


def load_horizons_ephems(object_name, ref_file, ref_mjd, filelist, params):

    if ((object_name.startswith("'") and object_name.endswith("'")) or
        (object_name.startswith('"') and object_name.endswith('"'))):
        object_name = object_name[1:-1]
    object_name = object_name.replace(".", " ")

    results = podi_ephemerides.get_ephemerides_for_object_from_filelist(
        object_name=object_name, 
        filelist=filelist,
        session_log_file="swarpstack_horizon.session",
        verbose=False
    )

    params['ephemerides'] = {
        'ref': ref_file,
        'ref-mjd': ref_mjd,
        'datafile': "NASA-TELNET",
        'data': results['data'],
        'ra': results['ra'],
        'dec': results['dec'],
        'target': object_name,
    }
    return

def get_reference_mjd(item):

    ref_obsid = None
    try:
        ref_mjd = float(item)
    except:
        if (os.path.isfile(item)):
            hdulist = pyfits.open(item)
            ref_obsid = hdulist[0].header['OBSID'] if 'OBSID' in hdulist[0].header else None
            for ext in hdulist:
                if ('MJD-OBS' in ext.header):
                    ref_mjd = ext.header['MJD-OBS']
                    # correct reference MJD to the mid-point of the exposure
                    if ('EXPTIME' in ext.header):
                        ref_mjd += ext.header['EXPTIME']/2./86400
                    break
            hdulist.close()
        else:
            ref_mjd = None

    return ref_mjd, ref_obsid


def read_swarp_params(filelist):

    params = {}

    logger = logging.getLogger("SwarpStack - Config")

    params['pixelscale'] = float(cmdline_arg_set_or_default("-pixelscale", 0))

    # params['subtract_back'] = cmdline_arg_isset("-bgsub")
    params['subtract_back'] = False
    if (cmdline_arg_isset("-bgsub")):
        params['subtract_back'] = cmdline_arg_set_or_default("-bgsub", 'swarp')

    params['reuse_singles'] = cmdline_arg_isset("-reusesingles")
    params['use_nonsidereal'] = cmdline_arg_isset("-nonsidereal")
    params['target_dimension'] = float(cmdline_arg_set_or_default('-dimension', -1))
    params['add'] = cmdline_arg_isset("-add")
    params['reference_file'] = cmdline_arg_set_or_default("-reference", None)
    params['no-fluxscale'] = cmdline_arg_isset('-nofluxscale')

    params['ignore_quality_checks'] = cmdline_arg_isset("-ignorechecks")

    combine_methods = cmdline_arg_set_or_default('-combine', 'weighted')
    params['combine-type'] = []
    for combine_method in combine_methods.split(","):
        if (not combine_method.lower() in ['average', 'median', 'sum', 'min', 'max', 'weighted', 'chi2',
                                           'chi-old', 'chi-mode', 'chi-mean', 'clipped',
                                           'weighted_weight', 'median_weight', 'and', 'nand', 'or', 'nor']):
            logger = logging.getLogger("Setup")
            logger.error("The specified combine method (%s) is not supported, using average instead" % (combine_method))
            continue
        else:
            params['combine-type'].append(combine_method.upper())
    if (params['combine-type'] == []):
        params['combine-type'].append('average'.upper())
    

    params['use_ephemerides'] = cmdline_arg_isset("-ephemerides")
    if (params['use_ephemerides']):
        opts = cmdline_arg_set_or_default('-ephemerides', None)
        # print opts
        if (opts == None):
            params['use_ephemerides'] = False
        else:
            items = opts.split(',')
            # print items

            # See if the first parameter is a number, if so it's the reference MJD,
            # if not, assume it's a FITS file and we are to read MJD-OBS from the header
            ref_mjd = None
            if (items[0] == 'NASA'):
                if (len(items) >= 3):
                    ref_file = items[2]
                else:
                    # Use the first input frame as reference frame
                    ref_file = get_clean_cmdline()[2]
                
                ref_mjd, ref_obsid = get_reference_mjd(ref_file)

                if (not ref_mjd == None):
                    object_name = items[1]
                    full_filelist = list(filelist)
                    full_filelist.append(ref_file)
                    load_horizons_ephems(object_name, ref_file, ref_mjd, full_filelist, params)
                    params['ephemerides']['mode'] = "telnet:horizons"
                    params['ephemerides']['ref-obsid'] = ref_obsid
                else:
                    logger.critical("Unable to find MJD reference file (%s)" % (ref_file))
                    raise RuntimeError("Unable to find MJD reference file (%s)" % (ref_file))

            elif (items[0] == 'file'):
                ref_file = get_clean_cmdline()[2] if len(items) <=3 else items[2]
                ref_mjd, ref_obsid = get_reference_mjd(ref_file)
                if (not ref_mjd == None):

                    # Now read and process the datafile
                    import ephemerides
                    ra, dec, data = ephemerides.load_ephemerides(
                        items[1], plot=False
                    )
                    logger.info("Using ephemerides from file %s" % (items[1]))

                    params['ephemerides'] = {
                        'ref': items[0],
                        'ref-mjd': ref_mjd,
                        'datafile': os.path.abspath(items[1]),
                        'data': data,
                        'ra': ra,
                        'dec': dec,
                        'mode': 'file:'+items[1],
                        'target': "user-defined",
                        'ref-obsid': ref_obsid,
                    }
            if (ref_mjd == None):
                params['use_ephemerides'] = False


    params['clip-ampfrac'] = 0.3
    params['clip-sigma'] = 4.0
    if (cmdline_arg_isset('-clip')):
        vals = get_cmdline_arg('-clip')
        if (len(vals)>0):
            items = vals.split(',')
            if (len(items) > 0):
                params['clip-sigma'] = float(items[0])
            if (len(items) >= 2):
                params['clip-ampfrac'] = float(items[1])

    params['mask-fits'] = None
    params['mask-npix'] = cmdline_arg_set_or_default("-masknpix", 5)
    params['mask-nsigma'] = cmdline_arg_set_or_default("-masknsigma", 2)
    params['mask-list'] = None
    params['mask-mjds'] = None
    if (cmdline_arg_isset("-maskframe")):
        vals = get_cmdline_arg("-maskframe").split(",")
        mask_list = []
        for filename in vals:
            if (os.path.isfile(filename)):
                mask_list.append(filename)
        if (mask_list): params['mask-fits'] = mask_list
    
    params['mask'] = None
    if (cmdline_arg_isset("-mask")):
        mask = get_cmdline_arg("-mask")
        if (os.path.isfile(mask)):
            params['mask'] = mask

    params['huge_frame_allowed'] = cmdline_arg_isset("-huge")

    params['target_magzero'] = cmdline_arg_set_or_default("-targetzp", 25.0)

    params['illumcorr'] = cmdline_arg_set_or_default("-illumcorr", None)
    params['illumcorrfiles'] = cmdline_arg_set_or_default("-illumcorrfiles", None)

    params['normalize_sky'] = cmdline_arg_isset("-normsky")

    return params

if __name__ == "__main__":
    if (len(sys.argv) <= 1):
        import podi_swarpstack as me
        print me.__doc__
        sys.exit(0)

    # Setup everything we need for logging
    options = set_default_options()
    podi_logging.setup_logging(options)

    logger = logging.getLogger("SwarpStack-Startup")
    if (cmdline_arg_isset("-fromfile")):
        configfile = get_cmdline_arg("-fromfile")
        if (os.path.isfile(configfile)):
            logger.info("Reading additional command line parameters from file (%s)" % (configfile))
            conf = open(configfile, "r")
            lines = conf.readlines()
            for line in lines:
                line = line.strip()
                if (len(line) <= 0):
                    continue
                elif (line.startswith("#")):
                    continue
                elif (line.startswith("-")):
                    sys.argv.append(line)
                else:
                    items = line.split()
                    sys.argv.append(items[0])
            conf.close()
        else:
            logger.error("Can't open the configfile (%s)" % (configfile))

    logger.debug("Reading options from command line")
    options = read_options_from_commandline(options)

    keep_intermediates = cmdline_arg_isset('-keep')
    unique_dir = None
    if (cmdline_arg_isset("-uniquedir")):
        unique_dir = get_cmdline_arg("-uniquedir")

    # print "non-sid",options['nonsidereal']
    try:

        # Read command line and store all results in params dictionary
        outputfile = get_clean_cmdline()[1]
        inputlist = []
        for f in get_clean_cmdline()[2:]:
            if (os.path.isfile(f)):
                inputlist.append(f)
        params = read_swarp_params(inputlist)
        
        # print params
        # print inputlist

        logger.debug("Commanding output: %s" % (outputfile))
        for i in inputlist:        
            logger.debug("Commanding input: %s" % (i))

        logger.debug("Starting processing")
        swarpstack(outputfile=outputfile, 
                   inputlist=inputlist, 
                   swarp_params=params, 
                   options=options,
                   keep_intermediates=keep_intermediates,
                   unique_dir=unique_dir)

    except KeyboardInterrupt, SystemExit:
        pass
    except RuntimeError:
        logger.critical("Encountered critical error, terminating execution! Please check command!")
        logger.debug("Terminating after runtime error!")
    except:
        if (len(get_clean_cmdline()) < 3):
            logger.error("Not enough parameters have been specified, need at least output file and 1 input file")
        else:
            podi_logging.log_exception()
        pass
    finally:
        podi_logging.shutdown_logging(options)
