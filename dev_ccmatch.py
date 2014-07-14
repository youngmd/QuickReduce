#!/usr/bin/env python

"""

This module contains all functionality to perform the astrometric correction of
a frame by matching the source catalog to a catalog of reference stars.

"""

import sys
import numpy
import os
import pyfits
import datetime
import scipy
import scipy.stats
import math
import scipy.spatial
import itertools

from podi_definitions import *
from podi_commandline import *
import podi_search_ipprefcat
from podi_wcs import *
import podi_search_ipprefcat
import podi_sitesetup as sitesetup

import Queue
import multiprocessing

max_pointing_error = 8.

import podi_logging
import logging

create_debug_files = False

def select_brightest(radec, mags, n):
    """ 

    Create a new catalog containing only the n brightest members of the input 
    catalog.

    """

    # print mags
    si = numpy.argsort(mags[:,0])
    # print si

    output_radec = numpy.zeros(shape=(n, radec.shape[1]))
    output_mags = numpy.zeros(shape=(n, mags.shape[1]))

    for i in range(n):
        # print si[i], mags[si[i],0]
        output_radec[i,:] = radec[si[i],:]
        output_mags[i,:] = mags[si[i],:]

    return output_radec, output_mags


def count_matches(src_cat, ref_cat, 
                  pointing_error=(max_pointing_error/60.), 
                  matching_radius=(4./3600.), debugangle=None):

    """

    This is the main routine in ccmatch. First, find for each source in catalog
    1 all nearby sources in catalog 2. In a second step, determine the
    approximate relative position that occurs the most frequently.

    """

    logger = logging.getLogger()

    # 
    # Now loop over all stars in the source catalog and find nearby stars in the reference catalog
    # Use a rather large matching radius for this step
    #
    # matching_radius = 1./60. # 1 arcmin

    # Fix the cos(declination) problem
    max_declination = numpy.max(numpy.fabs(src_cat[:,1]))
    if (max_declination > 85): max_declination = 85
    cos_dec = math.cos(math.radians(max_declination))
    ref_cat = ref_cat.copy()
    ref_cat[:,0] *= cos_dec
    src_cat = src_cat.copy()
    src_cat[:,0] *= cos_dec

    ref_tree = scipy.spatial.cKDTree(ref_cat)

    src_tree = scipy.spatial.cKDTree(src_cat)

    # print "\n\n\nIn count_matches:"
    # print "src-cat:",src_cat.shape
    # print "ref-cat:",ref_cat.shape

    #
    # First create a catalog of nearby reference stars for each source star
    #
    # find all matches
    matches = src_tree.query_ball_tree(ref_tree, pointing_error, p=2)
    # also count how many matches in total we have found
    n_matches = src_tree.count_neighbors(ref_tree, pointing_error, p=2)

    all_offsets = numpy.zeros(shape=(n_matches,2))
    cur_pair = 0

    # dummy = open("ccmatch.offsets.%d" % int(round((debugangle*60),0)), "w")

    for cur_src in range(len(matches)):
        if (len(matches[cur_src]) <= 0):
            continue

        #if (verbose): print "\n",cur_src
        # print matches[cur_src]
        #
        # matches[cur_src] contains the indices of matching stars from the reference catalog
        # So extract the actual coordinates of all nearby reference stars
        #
        cur_matches = numpy.array(ref_cat[matches[cur_src]])
        #if (verbose): print cur_matches

        # print cur_matches.shape

#        matches[cur_src] -= src_cat[cur_src]

        # Subtract the source position to get relative offsets
        cur_matches -= src_cat[cur_src]
        #if (verbose): print cur_matches
      
        #numpy.savetxt(dummy, cur_matches)
        #print >>dummy, "\n\n\n\n\n"

        # And add all offsets into the global offset registry
        for cur_refstar in range(len(matches[cur_src])):
            all_offsets[cur_pair,:] = cur_matches[cur_refstar]
            cur_pair += 1

    all_offsets = all_offsets[:cur_pair,:]
    # print "found", all_offsets.shape, "potential offsets", cur_pair, n_matches

    if (create_debug_files): numpy.savetxt("ccmatch.dump",all_offsets)

    # 
    # At this stage, we have a catalog of all potential offsets, 
    # so we now need to figure out which one is the most likely,
    # i.e. the most frequently occuring
    #
    # print "matching radius during count_matches:", matching_radius, matching_radius*3600
    candidate_offset_tree = scipy.spatial.cKDTree(all_offsets)
    n_coincidences = candidate_offset_tree.count_neighbors(
        candidate_offset_tree, matching_radius, p=2)
    coincidences = candidate_offset_tree.query_ball_tree(
        candidate_offset_tree, matching_radius, p=2)

    search_weights = numpy.zeros(shape=(len(coincidences),3))

    for i in range(len(coincidences)):
        search_weights[i,0] = len(coincidences[i])
        search_weights[i,1:3] = all_offsets[i,:]

    # search_weights = numpy.zeros(shape=(all_offsets.shape[0],3))
    # search_weights[:,1:3] = all_offsets
    # for i in range(all_offsets.shape[0]):
    #     neighbors = candidate_offset_tree.query_ball_point(search_weights[i,1:3], 1e-8, p=2)
    #     search_weights[i,0] = len(neighbors)
    #     #search_weights[i,0] = candidate_offset_tree.count_neighbors(
    #     #    scipy.spatial.cKDTree(search_weights[i,1:3]), (5./3600.), p=2)

    # Find which offset has the highest weight
    max_coincidence_count = numpy.argmax(search_weights[:,0])

    best_offset = all_offsets[max_coincidence_count,:]

    # So far all work was done with cos_dec fixed data, so revert this fix to 
    # yield real corrections
    best_offset[0] /= cos_dec

    #print "best offset", best_offset, "matching in",search_weights[max_coincidence_count][0],"fields"
    logger.debug("best offset %s matching in %d fields" % (
        best_offset, search_weights[max_coincidence_count][0]))

    if (not debugangle == None):
        if (create_debug_files): numpy.savetxt("ccmatch.offsetcount.%d" % int(round((debugangle*60),0)), search_weights)

    return search_weights[max_coincidence_count,0], best_offset


def rotate_shift_catalog(src_cat, center, angle, shift=None, verbose = False):
    """

    Apply a rotation and shift to a catalog.

    """
    if (verbose):
        print "\n\n\nIn rotate_shift_catalog"
        print "angle =", angle
        print "shift =", shift
        print "center =", center
        print "src-cat=\n", src_cat[:3]

    center_ra, center_dec = center
    # print center_ra, center_dec

    src_rotated = numpy.zeros(shape=(src_cat.shape[0],2))
    src_rel_to_center = src_cat[:,0:2] - [center_ra, center_dec] 

    # print "\n\n\nDuring rotation"
    # print "src-cat=\n",src_cat[:5,:]
    # print "src-cat rel to center=\n",src_rel_to_center[:5,:]
    # print "rotation end...\n\n"

    # angles are given in arcmin
    angle_rad = math.radians(angle)
    if (verbose): print "angle radians =",angle_rad

    # print "in rot_shift: angle-rad=",angle_rad

    # Account for cos(declination)
    src_rel_to_center[:,0] *= math.cos(math.radians(center_dec))

    if (verbose and not shift == None):
        print "@@@@ shift rotation"
        print "shift=", shift
        print "angle=", angle*60, "arcmin"
        print "X=",math.cos(angle_rad) * shift[0] - math.sin(angle_rad) * shift[1]
        print "y=",math.sin(angle_rad) * shift[0] + math.cos(angle_rad) * shift[1]

    # Apply rotation
    src_rotated[:,0] \
        = math.cos(angle_rad) * src_rel_to_center[:,0] \
        - math.sin(angle_rad) * src_rel_to_center[:,1]
    src_rotated[:,1] \
        = math.sin(angle_rad) * src_rel_to_center[:,0] \
        + math.cos(angle_rad) * src_rel_to_center[:,1] \

    # Fix cos(declination)
    src_rotated[:,0] /= math.cos(math.radians(center_dec))

    # Add center position
    src_rotated += [center_ra, center_dec]

    # if requested, add shift
    if (not shift == None):
        src_rotated += shift
    
    if (verbose): 
        print "src_rotated=\n", src_rotated[:3,0:2]
        print "src-final=\n", src_rotated[:3],"\n\n\n"

    src_output = src_cat.copy()
    src_output[:,0:2] = src_rotated[:,0:2]

    return src_output

def kd_match_catalogs(src_cat, ref_cat, matching_radius, max_count=1):
    """

    Match two catalogs using kD-trees.

    Parameters:

    src_cat : ndarray

        input catalog 1. first two columns have to be Ra/Dec

    ref_cat : ndarray

        input catalog 2. again, columns 1 &2 have to be Ra/Dec.

    matching_radius : float

        matching radius in arcsec. If two sources are closer than this, they are 
        considered a match.

    max_count : int

        Exclude all sources that have more than (max_count) matches.

    Returns
    -------

        The matched catalog. The columns of this output catalog first contain
        all columns from the input catalog 1, followed by all columns of the
        matched sources in catalog 2. If no counterpart is found in catalog 2,
        this source is omitted from the output catalog.

    Currently only the first match is returned for each input source.

    """
    
    src_cat = src_cat.copy()
    ref_cat = ref_cat.copy()
    max_declination = numpy.max(numpy.fabs(ref_cat[:,1]))
    if (max_declination > 85): max_declination = 85
    cos_dec = math.cos(math.radians(max_declination))
    src_cat[:,0] *= cos_dec
    ref_cat[:,0] *= cos_dec

    src_tree = scipy.spatial.cKDTree(src_cat[:,0:2])
    ref_tree = scipy.spatial.cKDTree(ref_cat[:,0:2])

    # print src_cat[0:5]
    # print ref_cat[0:5]

    # Create an array to hold the matched catalog
    output_cat = numpy.empty(shape=(src_cat.shape[0], src_cat.shape[1]+ref_cat.shape[1]))
    # and insert the source catalog
    n_src_columns = src_cat.shape[1]
    output_cat[:,0:n_src_columns] = src_cat

    # print output_cat[0:5]

    # also create an array holding for which sources we found a match
    match_found = numpy.zeros(shape=(src_cat.shape[0]))

    # match the catalogs using a kD-tree
    match_indices = src_tree.query_ball_tree(ref_tree, matching_radius, p=2)

    # print src_tree.count_neighbors(ref_tree, matching_radius, p=2)

    # Now loop over all matches and merge the found matches
    for cur_src in range(src_cat.shape[0]):

        # Determine how many reference stars are close to this source
        # Do not keep match if none or too many reference stars are nearby
        n_matches = len(match_indices[cur_src])
        if (n_matches <= 0 or n_matches > max_count):
            continue
    
        output_cat[cur_src, n_src_columns:] = ref_cat[match_indices[cur_src][0]]
        match_found[cur_src] = 1

    # Now eliminate all sources without matches
    final_cat = output_cat[match_found == 1]

    # Reverse the cos_declination fix from above
    final_cat[:,0] /= cos_dec
    final_cat[:,n_src_columns] /= cos_dec

    return final_cat



def count_matches_parallelwrapper(work_queue, return_queue,
                                  src_cat, ref_cat, 
                                  center_ra, center_dec,
                                  pointing_error=(max_pointing_error/60.), 
                                  matching_radius=(4./3600.), 
                                  debugangle=None
                                  ):
    """
    
    Just a small wrapper to enable parallel execution of `count_matches`.

    """

    logger = logging.getLogger()
    while (True):
        task = work_queue.get()
        if (task == None):
            break

        angle_id, angle = task

        logger.debug("Starting work on angle %f deg / %f arcmin" % (angle,angle*60))
        # print "Starting work on angle",angle,angle*60,"(deg/arcmin)"

        src_rotated = rotate_shift_catalog(src_cat, (center_ra, center_dec), angle, None)

        # print "Angle:",angle*60.," --> ",
        n_matches, offset = count_matches(src_rotated, ref_cat, 
                                          pointing_error=pointing_error, 
                                          matching_radius=matching_radius,
                                          debugangle=angle)

        if (create_debug_files):
            numpy.savetxt("ccmatch.cat%f" % (angle*60), src_rotated)
            print angle*60,offset
            matched_cat = kd_match_catalogs(src_rotated[:,0:2]+offset, ref_cat[:,0:2], matching_radius, max_count=1)
            numpy.savetxt("ccmatch.matched_%f" % (angle*60), matched_cat)

        return_queue.put((angle_id, n_matches, offset))
        work_queue.task_done()

    return

def find_best_guess(src_cat, ref_cat,
                    center_ra, center_dec,
                    pointing_error=(max_pointing_error/60.),
                    angle_max=5., #degrees
                    d_angle=3, # arcmin
                    matching_radius=5./3600.,
                    allow_parallel=True,
                    ):
    """Find the best-guess astrometric correction by finding the shift and rotation 
    angle that yields the most matching stars by iterating over a number of 
    possible rotator angles.

    Parameters
    ----------

    src_cat : ndarray
    
        Catalog of sources in ODI image

    ref_cat : ndarray

        Reference catalog of stars from, e.g., 2MASS

    center_ra : double

        center of rotation in RA - this has to be CRVAL1 to make the solution
        compatible with the FITS WCS convention

    center_dec : double
    
        center of rotation in Dec - has to be CRVAL2

    pointing_error : double

        Maximum positional uncertainty, in degrees. Larger is safer, but takes
        more time to compute and doesn't help if pointing errors are small.

    angle_max : double or double[2]

        Maximum uncertainty in the rotator angle position, in degrees. Can
        either be a single angle or a float[2], e.g. [-1,2].

    matching_radius : double

        radius to use when computing the density of overlapping points. Smaller
        numbers give slightly more accurate results, but larger values are 
        more reliable when frames show some distortion.

    allow_parallel : Bool

        Run the catalog matching in parallel (faster, recommended) or as a 
        single process (slower, needs fewer resources)

    Returns
    -------
    The best_guess shift and rotation angle
    
    """

    # 
    # Now loop over all stars in the source catalog and find nearby stars in the reference catalog
    # Use a rather large matching radius for this step
    #
    #matching_radius = 1./60. # 1 arcmin
    ref_tree = scipy.spatial.cKDTree(ref_cat)

    #angle_max = 2.
    #d_angle = 5.

    logger = logging.getLogger('findbestguess')

    if (angle_max == None):
        # This means there's no rotation at all
        n_angles = 1
        all_results = numpy.zeros(shape=(1, 4))
        all_results[0,0] = 0.
        
    elif (type(angle_max) == int or type(angle_max) == float): 
        # Just a number given, assume the range is from -x to +x
        n_angles = int(math.ceil((2 * angle_max) / (d_angle / 60.))) + 1
        all_results = numpy.zeros(shape=(n_angles, 4))
        all_results[:,0] = numpy.linspace(-angle_max, angle_max, n_angles)

    elif (len(angle_max) == 2):
        # Two angles given, interpret them as min and max
        n_angles = int(math.ceil((angle_max[1] - angle_max[0]) / (d_angle / 60.))) + 1
        all_results = numpy.zeros(shape=(n_angles, 4))
        all_results[:,0] = numpy.linspace(angle_max[0], angle_max[1], n_angles)

    else:
        print "We don't know how to handle this case"
        print "in find_best_guess, angle_max =",angle_max
        sys.exit(0)


    if (allow_parallel):

        processes = []
        queue = multiprocessing.JoinableQueue()
        return_queue = multiprocessing.Queue()
        
        # Feed all angles to check into the queue
        for cur_angle in range(n_angles):
            angle = all_results[cur_angle,0]
            queue.put((cur_angle, angle))

        # worker_args = (queue, return_queue,
        #                src_cat, ref_cat, center_ra, center_dec,
        #                matching_radius,
        #                fine_radius, 
        #                angle)
        worker_args = {
            "work_queue": queue, 
            "return_queue": return_queue,
            "src_cat": src_cat, 
            "ref_cat": ref_cat, 
            "center_ra": center_ra, 
            "center_dec": center_dec,
            "pointing_error": pointing_error,
            "matching_radius": matching_radius,
            "debugangle": None,
        }
                                  

        number_cpus = sitesetup.number_cpus
        logger.debug("Running ccmatch on %d CPUs, hold on ..." % (number_cpus))
        for i in range(number_cpus):
            p = multiprocessing.Process(target=count_matches_parallelwrapper, kwargs=worker_args)
            p.start()
            processes.append(p)

            # Also send a quit signal to each process
            queue.put(None)
        
        # And finally, collect all results
        for i in range(n_angles):

            returned = return_queue.get()
            cur_angle, n_matches, offset = returned

            all_results[cur_angle,1:3] = offset
            all_results[cur_angle,3] = n_matches

        # Join all processes to make sure they terminate alright 
        # without leaving zombie processes behind.
        for p in processes:
            p.join()


    else:

        for cur_angle in range(n_angles):
            angle = all_results[cur_angle,0]
            logger.debug("Working on angle %f deg / %f arcmin" % (angle,angle*60))

            src_rotated = rotate_shift_catalog(src_cat, (center_ra, center_dec), angle, None)
            logger.debug("Angle: %f -->" % (angle*60.))
            numpy.savetxt("ccmatch.cat%f" % (angle*60), src_rotated)
            n_matches, offset = count_matches(src_rotated, ref_cat, matching_radius, 
                                              fine_radius=fine_radius,
                                              debugangle=angle)

            all_results[cur_angle,1:3] = offset
            all_results[cur_angle,3] = n_matches

    
    logger.debug(all_results)
    if (create_debug_files): numpy.savetxt("ccmatch.allresults", all_results)


    #
    # Now find the best solution (the one with the highest matched star density)
    #
    idx_best_angle = numpy.argmax(all_results[:,3])

    best_guess = all_results[idx_best_angle]
    logger.debug("Best guess: angle=%f arcmin" % (best_guess[0]*60.))
    logger.debug(best_guess)
    # print best_guess, "angle=",best_guess[0]*60.,"arcmin"

    #
    # Also determine a contrast as quality estimator 
    #
    best_angle = best_guess[0]
    # select all results with rotator angles differing by >20 arcmin
    wrong_angles = numpy.fabs(all_results[:,0]-best_angle) > 20./60.

    # number random matches:
    if (numpy.sum(wrong_angles) <= 0):
        n_random_matches = 1
    else:
        n_random_matches = numpy.median(all_results[:,3][wrong_angles])

    # contrast = best_guess[3] / n_random_matches
    if (n_random_matches >= 1):
        contrast = (best_guess[3]-n_random_matches) / math.sqrt(n_random_matches)
    else:
        contrast = best_guess[3]
    
    return best_guess, n_random_matches, contrast





def fit_best_rotation_shift(src_cat, ref_cat, 
                            best_guess,
                            center_ra, center_dec,
                            matching_radius=(6./3600.)
                            ):

    """

    optimize the astroemtric solution by minimizing the difference betweeen 
    source and reference positions in a matched catalog.

    """
    logger = logging.getLogger("OptimizeShiftRotation")

    # print "initial best guess before optimizing", best_guess
    src_rotated = rotate_shift_catalog(src_cat, 
                                       (center_ra, center_dec), 
                                       angle=best_guess[0], 
                                       shift=best_guess[1:3])

    if (create_debug_files): numpy.savetxt("ccmatch.roughalign", src_rotated)
    
    if (create_debug_files):
        numpy.savetxt("ccm.src", src_cat)
        numpy.savetxt("ccm.ref", ref_cat)
        print "src-cat:", src_cat[:5]
        print "ref-cat:", ref_cat[:5]

    #
    # Fix the cos(declination) problem
    # Important when using these fixed coordinates: Undo this correction 
    # at the end to returning offsets in true RA and DEC !!!!!
    #
    max_declination = numpy.max(numpy.fabs(src_cat[:,1]))
    if (max_declination > 85): max_declination = 85
    cos_dec = math.cos(math.radians(max_declination))
    ref_cat_cosdec = ref_cat.copy()
    ref_cat_cosdec[:,0] *= cos_dec
    src_rotated[:,0] *= cos_dec

    if (create_debug_files):
        numpy.savetxt("ref", ref_cat_cosdec)
        numpy.savetxt("src", src_rotated)

    #
    # Match up stars from the source and reference catalog. Once we have a 
    # matched catalog we can optimize the WCS to minimize the errors between
    # stars in each of the catalogs.
    #
    logger.debug("Matching catalogs")

    ref_tree = scipy.spatial.cKDTree(ref_cat_cosdec)
    src_tree = scipy.spatial.cKDTree(src_rotated)
    matched_src_ref_idx = src_tree.query_ball_tree(ref_tree, matching_radius, p=2)

    src_ref_pairs = numpy.ones(shape=(src_rotated.shape[0],4))
    src_ref_pairs[:,0:2] = src_rotated[:,0:2]
    src_ref_pairs[:,2:4] = numpy.NaN

    #
    # Merge the two catalogs to make fitting easier
    # Ignore all points with no or more than 1 closest match
    #
    # Important: While we match catalogs based on cos(dec)-fixed coordinates,
    # the matched catalog contains the uncorrected coordinates. This is 
    # important to get real Ra/Dec offsets!
    #
    logger.debug("Merging catalogs for easier fitting")
    for i in range(len(matched_src_ref_idx)):
        n_close_stars = len(matched_src_ref_idx[i])
        if (not n_close_stars == 1):
            continue
        src_ref_pairs[i, 2:4] = ref_cat[matched_src_ref_idx[i][0]]

    if (create_debug_files): numpy.savetxt("ccmatch.srcrefmatched", src_ref_pairs)

    #
    # Further optimize the rotation angle by introducing the 
    # shift and rotation as free parameters and fitting to minimize 
    # the deviations
    #
    p_init = [best_guess[0], best_guess[1], best_guess[2]]
    logger.debug("Minimizing offsets between source- and reference catalog")
    logger.debug("Initial guess (angle, dx, dy): %s" % (str(p_init)))


    # This is the function that computes the errors
    # This is operating on real Ra/Dec data, so correct for cos(declination)
    def difference_source_reference_cat(p, src_cat, ref_cat, center, for_fitting=False):
        src_rotated = rotate_shift_catalog(src_cat, center, 
                                           angle=p[0], 
                                           shift=p[1:3])
        diff = src_rotated - ref_cat
        diff[:,0] *= numpy.cos(numpy.radians(ref_cat[:,1]))
        if (for_fitting):
            return diff.ravel()

        return diff

    # 
    # Eliminate all source stars without nearby/unique 
    # match in the reference catalog
    #
    valid_matches = numpy.isfinite(src_ref_pairs[:,2])
    matched_src = src_cat[valid_matches]
    matched_ref = src_ref_pairs[:,2:4][valid_matches]

    center_radec = (center_ra, center_dec)
    args = (matched_src, matched_ref, center_radec, True)
    if (create_debug_files):
        numpy.savetxt("ccm.matched_src", matched_src)
        numpy.savetxt("ccm.matched_ref", matched_ref)

    fit = scipy.optimize.leastsq(difference_source_reference_cat, 
                                 p_init, 
                                 args=args, 
                                 full_output=1)

    best_fit = fit[0]

    logger.debug("optimized parameters: " + str(best_fit))

    # print "\n\nbefore/after fit"
    # print p_init
    # print fit[0]
    # # Compute uncertainty on the shift and rotation
    # uncert = numpy.sqrt(numpy.diag(fit[1]))
    # print uncert
    # best_shift_rotation_solution = fit[0]


    diff_afterfit = difference_source_reference_cat(fit[0], 
                                                    matched_src, 
                                                    matched_ref, 
                                                    center_radec, 
                                                    for_fitting=False)
    
    if (create_debug_files): numpy.savetxt("ccmatch.diff_afterfit", diff_afterfit)

    return_value = [best_fit[0], 
                    best_fit[1], best_fit[2], 
                    numpy.sum(valid_matches)
    ]

    return return_value




def optimize_shift_rotation(p, guessed_match, hdulist, fitting=True):
    
    """
    outdated, don't use.
    """

    diff = numpy.zeros(shape=(guessed_match.shape[0],2))

    n_start = 0

    for ext in range(3): #len(hdulist)):
        if (not is_image_extension(hdulist[ext])):
            continue

        ota_extension = hdulist[ext]
        ota = int(ota_extension.header['FPPOS'][2:4])
        
        # sources from this OTA
        in_this_ota = (guessed_match[:,8] == ota)
        number_src_in_this_ota = numpy.sum(in_this_ota)
        # print number_src_in_this_ota
        if (number_src_in_this_ota <= 0):
            continue

        ota_cat = guessed_match[in_this_ota]

        # Read the WCS imformation from the fits file
        wcs_poly = header_to_polynomial(ota_extension.header)

        # And apply the current shift and rotation values
        wcs_poly = wcs_apply_rotation(wcs_poly, p[0])
        wcs_poly = wcs_apply_shift(wcs_poly, p[1:3])

        # hdr = ota_extension.header.copy()
        # wcs_wcspoly_to_header(wcs_poly, hdr)
        # hdr['CTYPE1'] = 'RA---TPN'
        # hdr['CTYPE2'] = 'DEC--TPN'
        # wcs = astWCS.WCS(hdr, mode='pyfits')
        # ra_dec = numpy.array(wcs.pix2wcs(ota_cat[:,2], ota_cat[:,3]))
        # print ra_dec.shape, number_src_in_this_ota

        # Extract only the stars in this OTA - 
        # only for these is the WCS solution applicable
        ota_cat = guessed_match[in_this_ota]

        # Convert pixel coordinates into Ra/Dec
        ra_dec = wcs_pix2wcs(ota_cat[:,2:4], wcs_poly, False)


        
        # And compute the offset between ODI and reference catalog
        ota_diff = ra_dec - ota_cat[:,0:2] #ota_cat[:,-2:]

        # and save differences until we have all of them
        diff[n_start:n_start+number_src_in_this_ota] = ota_diff
        
        n_start += number_src_in_this_ota

    if (not fitting):
        return diff

    if (create_debug_files): 
        x = open("ccmatch.wcsfitting", "a")
        numpy.savetxt(x, diff)
        print >>x, "\n\n\n\n\n"
        x.close()
        
        y = open("ccmatch.fitparams", "a")
        print >>y, p[0], p[1], p[2]
        y.close()
        
    
    return diff.ravel()



def verify_wcs_model(cat, hdulist):
    """
    for debugging only, don't use
    """
    comp = numpy.zeros(shape=(cat.shape[0],8))

    n_start = 0

    for ext in range(len(hdulist)):
        if (not is_image_extension(hdulist[ext])):
            continue

        ota_extension = hdulist[ext]
        extname = hdulist[ext].header['EXTNAME']
        ota = int(ota_extension.header['FPPOS'][2:4])

        # sources from this OTA
        in_this_ota = (cat[:,8] == ota)
        number_src_in_this_ota = numpy.sum(in_this_ota)
        # print number_src_in_this_ota
        if (number_src_in_this_ota <= 0):
            continue

        # Read the WCS imformation from the fits file
        wcs_poly = header_to_polynomial(ota_extension.header)

        # wcs_poly = wcs_clear_distortion(wcs_poly)


        # Extract only the stars in this OTA - 
        # only for these is the WCS solution applicable
        ota_cat = cat[in_this_ota]
        ota_cat[:,2:4] -= [1,1]

        # Convert pixel coordinates into Ra/Dec
        ra_dec = wcs_pix2wcs(ota_cat[:,2:4], wcs_poly)
        
        comp[n_start:n_start+number_src_in_this_ota,0:4] = ota_cat[:,0:4]
        comp[n_start:n_start+number_src_in_this_ota,4:6] = ra_dec[:,0:2]

        ota_extension.header['CTYPE1'] = 'RA---TPV'
        ota_extension.header['CTYPE2'] = 'DEC--TPV'
        wcs = astWCS.WCS(ota_extension.header, mode='pyfits')
        wcs2 = numpy.array(wcs.pix2wcs(ota_cat[:,2], ota_cat[:,3]))
        
        #print wcs2.shape
        #print wcs2[0:4,0:2]
        comp[n_start:n_start+number_src_in_this_ota,6:8] = wcs2[:,0:2]

        dump_file = "debug.verifywcs."+extname
        print "writing",dump_file
        numpy.savetxt(dump_file, comp[n_start:n_start+number_src_in_this_ota,:])

        n_start += number_src_in_this_ota

    return comp


def optimize_wcs_solution(ota_cat, hdr, optimize_header_keywords):

    """

    Optimize the WCS by allowing the given set of header keywords to vary.

    """

    # Create a astLib WCS class to handle the conversion from X/Y to Ra/Dec
    astwcs = astWCS.WCS(hdr, mode='pyfits')

    def minimize_wcs_error(p, src_xy, ref_radec, astwcs, optimize_header_keywords):

        # Transfer all fitting parameters to astWCS
        for i in range(len(optimize_header_keywords)):
            astwcs.header[optimize_header_keywords[i]] = p[i]
        # and update astWCS so the changes take effect
        astwcs.updateFromHeader()

        # Now compute all Ra/Dec values based on the new WCS solution
        src_radec = numpy.array(astwcs.pix2wcs(src_xy[:,0], src_xy[:,1]))

        # This gives us the Ra/Dec values as 2-d array
        # compute difference from the Ra/Dec of the reference system
        src_ref = src_radec - ref_radec

        # return the 1-d version for optimization
        return src_ref.ravel()

    # Prepare all values we need for fitting
    src_xy = ota_cat[:,2:4] - [1.,1.]
    ref_radec = ota_cat[:,-2:]

    p_init = [0] * len(optimize_header_keywords)
    for i in range(len(optimize_header_keywords)):
        p_init[i] = hdr[optimize_header_keywords[i]]

    fit_args = (src_xy, ref_radec, astwcs, optimize_header_keywords)
    fit = scipy.optimize.leastsq(minimize_wcs_error,
                                 p_init, 
                                 args=fit_args,
                                 maxfev=1000,
                                 full_output=1)

    # New, optimized values are in fit[0]
    better_wcs = fit[0]
    # Copy the optimized values into the header
    for i in range(len(optimize_header_keywords)):
        hdr[optimize_header_keywords[i]] = better_wcs[i]

    return p_init, better_wcs




def optimize_shear_and_position(ota_cat, hdr):
    """

    Optimize the WCS by allowing the CRVAL and CD matrix as free parameters

    """

    # Create a astLib WCS class to handle the conversion from X/Y to Ra/Dec
    astwcs = astWCS.WCS(hdr, mode='pyfits')

    keyword_order = ('CRVAL1', 'CRVAL2',
                     'CD1_1', 'CD1_2', 'CD2_1', 'CD2_2')

    def fit_shear_and_position(p, src_xy, ref_radec, astwcs):

        # Transfer all fitting parameters to astWCS
        for i in range(6):
            astwcs.header[keyword_order[i]] = p[i]
        # and update astWCS so the changes take effect
        astwcs.updateFromHeader()

        # Now compute all Ra/Dec values based on the new WCS solution
        src_radec = numpy.array(astwcs.pix2wcs(src_xy[:,0], src_xy[:,1]))

        # This gives us the Ra/Dec values as 2-d array
        # compute difference from the Ra/Dec of the reference system
        src_ref = src_radec - ref_radec

        # return the 1-d version for optimization
        return src_ref.ravel()

    # Prepare all values we need for fitting
    src_xy = ota_cat[:,2:4] - [1.,1.]
    ref_radec = ota_cat[:,-2:]

    p_init = [0] * 6
    for i in range(6):
        p_init[i] = hdr[keyword_order[i]]

    fit_args = (src_xy, ref_radec, astwcs)
    fit = scipy.optimize.leastsq(fit_shear_and_position,
                                 p_init, 
                                 args=fit_args, 
                                 full_output=1)

    # New, optimized values are in fit[0]
    better_wcs = fit[0]
    # Copy the optimized values into the header
    for i in range(6):
        hdr[keyword_order[i]] = better_wcs[i]

    return

def ccmatch_shift(source_cat, 
                  reference_cat,
                  center=None, #[center_ra, center_dec],
                  pointing_error=(max_pointing_error/60.), #(max_pointing_error/60.)
                  ):

    """

    Perform a simple astrometric calibration, allowing for a shift only

    """
    if (center == None):
        center_ra = numpy.median(source_cat[:,0])
        center_dec = numpy.median(source_cat[:,1])
    else:
        center_ra, center_dec = center

    best_guess, n_random_matches, contrast = find_best_guess(source_cat, 
                                 reference_cat,
                                 center_ra, center_dec,
                                 pointing_error=pointing_error,
                                 angle_max=None,
                                 allow_parallel=False,
                                 )

    logger = logging.getLogger("CCMatchShift")
    logger.debug("found best guess:")
    logger.debug(best_guess)
    logger.debug("offset="+str(best_guess[1:3]*3600.))

    return best_guess, n_random_matches, contrast






 
def log_shift_rotation(hdulist, params, n_step=1, description="",
                       n_random_matches=None,
                       wcs_contrast = None,
                   ):
    """

    Add some additional keywords to primary FITS header to keep track of the 
    shift and rotation found during astrometric calibration of the frame.

    """
    hdulist[0].header['WCS%d_DA'  % n_step] = (params[0], "%s angle [deg]" % (description))
    hdulist[0].header['WCS%d_DRA' % n_step] = (params[1]*3600., "%s d_RA [arcsec]" % (description))
    hdulist[0].header['WCS%d_DDE' % n_step] = (params[2]*3600, "%s d_DEC [arcsec]" % (description))
    hdulist[0].header['WCS%d_N'   % n_step] = (params[3], "%s n_matches" % (description))

    if (not n_random_matches == None):
        hdulist[0].header['WCS_NRND'] = (n_random_matches, "number of random matches")
    if (not wcs_contrast == None):
        hdulist[0].header['WCS_QUAL'] = (wcs_contrast, "WCS quality")

    return




def apply_correction_to_header(hdulist, best_guess, verbose=False):
    """

    Apply the optimzied shift and rotation found during astrometric calibration 
    to the output FITS header.

    """
    
    for ext in range(len(hdulist)):
        if (not is_image_extension(hdulist[ext])):
            continue

        ota_extension = hdulist[ext]
        
        # Read all WCS relevant information from the FITS header
        print "\nApplying shift",best_guess[1:3],"to extension",ota_extension.header['EXTNAME']
        print hdulist[ext].header['CRVAL1'], hdulist[ext].header['CRVAL2']
        wcs_poly = header_to_polynomial(ota_extension.header)

        # Apply the shift and rotation
        wcs_poly = wcs_apply_shift(wcs_poly, best_guess[1:3])

        # Write the updated, WCS relevant keywords back to FITS header
        wcs_wcspoly_to_header(wcs_poly, hdulist[ext].header) #ota_extension.header)

        if (verbose):
            print hdulist[ext].header['CRVAL1'], hdulist[ext].header['CRVAL2']
            hdulist[ext].header['XVAL1'] = hdulist[ext].header['CRVAL1']
            hdulist[ext].header['XVAL2'] = hdulist[ext].header['CRVAL2']

    return





def pick_isolated_stars(catalog, radius=10.):
    """

    Break down the catalog and eliminate all sources with nearby neighbors.

    """

    kdtree = scipy.spatial.cKDTree(catalog[:,0:2])

    # Create an array holding for which sources we found a match
    isolated = numpy.zeros(shape=(catalog.shape[0]))
    
    # match the catalogs using a kD-tree
    matching_radius = radius / 3600.
    match_indices = kdtree.query_ball_tree(kdtree, matching_radius, p=2)
    #match_count = kdtree.count_neighbors(kdtree, matching_radius, p=2)

    # print match_count

    # Now loop over all matches and merge the found matches
    for cur_src in range(catalog.shape[0]):

        # Determine how many reference stars are close to this source
        # Do not keep match if none or too many reference stars are nearby
        #
        # Keep in mind that each source has at least 1 nearby source: itself
        # so only neighbor counts > 1 count as having a real neighbor
        n_matches = len(match_indices[cur_src])
        if (n_matches <= 1):
            isolated[cur_src] = 1

        # print cur_src, catalog[cur_src, 0], catalog[cur_src,1], n_matches
    
    # Now eliminate all sources without matches
    # final_cat = catalog[match_count < 1] #isolated == 1]
    final_cat = catalog[isolated == 1]

    return final_cat



def parallel_optimize_wcs_solution(queue_in, queue_out):
    """

    This is a minimal wrapper around optimize_wcs_solution to enable parallel
    execution.

    All input commands are received via a multiprocessing.JoinableQueue and 
    reported back via a separate multiprocessing.Queue.

    """

    logger = logging.getLogger("ParallelOptimizeWCS")

    while (True):

        data_in = queue_in.get()
        if (data_in == None):
            logger.debug("Received end signal, shutting down")
            queue_in.task_done()
            return
            
        # Extract all necessary data from command queue
        catalog, header, headers_to_optimize, extension_id = data_in

        logger.debug("Starting work for OTA %s..." % (header['EXTNAME']))

        optimize_wcs_solution(catalog, header, headers_to_optimize)

        # Now that we have the optimized WCS solution, recompute the source 
        # Ra/Dec values with the better system
        astwcs = astWCS.WCS(header, mode='pyfits')

        ota_xy = catalog[:,2:4] - [1.,1.]
        ota_radec = numpy.array(astwcs.pix2wcs(ota_xy[:,0], ota_xy[:,1]))

        catalog[:,0:2] = ota_radec

        # Prepare and return results to main process
        return_data = catalog, header, extension_id
        queue_out.put(return_data)
        queue_in.task_done()
        logger.debug("Down with work for OTA %s..." % (header['EXTNAME']))

    return



def recompute_radec_from_xy(hdulist, src_catalog):

    global_cat = None

    #
    # Now re-compute the OTA source catalog including the updated WCS solution
    #
    for ext in range(len(hdulist)):
        if (not is_image_extension(hdulist[ext])):
            continue

        ota = hdulist[ext].header['OTA']
        in_this_ota = src_catalog[:,8] == ota

        ota_full = src_catalog[in_this_ota]
        astwcs = astWCS.WCS(hdulist[ext].header, mode='pyfits')

        ota_xy = ota_full[:,2:4] - [1.,1.]
        ota_radec = numpy.array(astwcs.pix2wcs(ota_xy[:,0], ota_xy[:,1]))

        ota_full[:,0:2] = ota_radec

        global_cat = ota_full if (global_cat == None) else numpy.append(global_cat, ota_full, axis=0)

    return global_cat


def improve_wcs_solution(src_catalog, 
                         ref_catalog,
                         hdulist,
                         headers_to_optimize,
                         matching_radius=(3./3600),
                         min_ota_catalog_size=15,
                         output_catalog = None,
                         allow_parallel = True,
                         ):
    """

    This function is a wrapper around the optimize_wcs_solution routine. It
    splits up the full catalog into sub-catalogs for each OTA, and optimizes
    each catalog by fiddling with some of the WCS keywords until the distance
    between source coordiantes and reference coordiantes is minimized.

    """

    logger = logging.getLogger("ImproveWCSSolutionOTA")

    # Match the entire input catalog with the reference catalog
    # Allow a matching radius of 3'', but only unique matches
    matched_global = kd_match_catalogs(src_catalog, 
                                       ref_catalog, 
                                       matching_radius=matching_radius, 
                                       max_count=1)

    global_cat = None

    # Prepare what we need for the parallel execution
    processes = []
    queue_cmd = multiprocessing.JoinableQueue()
    queue_return = multiprocessing.Queue()
    number_tasks = 0

    logger.debug("Running improve_wcs_solution in %s mode!" % ("parallel" if allow_parallel else "serial"))

    for ext in range(len(hdulist)):
        if (not is_image_extension(hdulist[ext])):
            continue

        ota = hdulist[ext].header['OTA']
        in_this_ota = matched_global[:,8] == ota

        ota_cat = matched_global[in_this_ota]
        logger.debug("OTA %d: % 4d sources, have >= % 4d for this step : %s" % (
            ota, ota_cat.shape[0], min_ota_catalog_size, "yes" if ota_cat.shape[0] > min_ota_catalog_size else "no"))


        # Don't optimize if we have to few stars to constrain solution
        if (ota_cat.shape[0] > min_ota_catalog_size):

            if (not allow_parallel):
                #
                # This is the work to be done serially
                #

                optimize_wcs_solution(ota_cat, hdulist[ext].header, headers_to_optimize)

            else:
                #
                # Do the work in parallel
                #
                task = (ota_cat, hdulist[ext].header, headers_to_optimize, ext)
                queue_cmd.put(task)
                number_tasks += 1


    if (allow_parallel):
        worker_args = {'queue_in': queue_cmd,
                       'queue_out': queue_return,
                       }
        # All work is queued, start the processes to do the work
        for i in range(sitesetup.number_cpus):
            p = multiprocessing.Process(target=parallel_optimize_wcs_solution, kwargs=worker_args)
            p.start()
            processes.append(p)

            # Also send a quit signal to each process
            queue_cmd.put(None)

        # Receive all results
        for i_results in range(number_tasks):
            catalog, header, extension_id = queue_return.get()

            # Merge the catalogs
            # global_cat = catalog if (global_cat == None) else numpy.append(global_cat, catalog, axis=0)

            # And re-insert the updated header
            hdulist[extension_id].header = header

        # Wait until all work is complete
        for p in processes:
            p.join()

  
    #
    # Now re-compute the OTA source catalog including the updated WCS solution
    #
    for ext in range(len(hdulist)):
        if (not is_image_extension(hdulist[ext])):
            continue

        ota = hdulist[ext].header['OTA']
        in_this_ota = src_catalog[:,8] == ota

        if (numpy.sum(in_this_ota) <= 0):
            continue

        ota_full = src_catalog[in_this_ota]
        astwcs = astWCS.WCS(hdulist[ext].header, mode='pyfits')

        ota_xy = ota_full[:,2:4] - [1.,1.]
        ota_radec = numpy.array(astwcs.pix2wcs(ota_xy[:,0], ota_xy[:,1]))

        ota_full[:,0:2] = ota_radec

        global_cat = ota_full if (global_cat == None) else numpy.append(global_cat, ota_full, axis=0)

  
    # Match the new, improved catalog with the reference catalog
    # Allow a matching radius of 2'', but only unique matches
    logger.debug("Matching optimized source catalog to reference catalog")
    matched_global = kd_match_catalogs(global_cat, 
                                       ref_catalog, 
                                       matching_radius=(2./3600.), 
                                       max_count=1)

    if (not output_catalog == None and create_debug_files):
        numpy.savetxt(output_catalog, matched_global)

    # print "Returning from improve_wcs_solution", src_catalog.shape, global_cat.shape, matched_global.shape

    return global_cat, hdulist, matched_global


#############################################################################
#############################################################################
#
#
#
#############################################################################
#############################################################################

def ccmatch(source_catalog, reference_catalog, input_hdu, mode,
            max_pointing_error=7,
            max_rotator_error=[-3,3.5],
            min_contrast=3.0):

    """

    Perform the astrometric correction.

    Parameters
    ----------

    source_catalog : ndarray

        Catalog of sources in the ODI frames. This needs to be in the 
        proper format, as ccmatch splits the catalog by OTA and eliminates 
        sources with flags.

    reference_catalog : ndarray

        Astrometric reference catalog. The first two columns have to be Ra/Dec.

    input_hdu : HDUList

        HDUlist containing the frame to be calibrated.

    mode : string

        Calibration mode for ccmatch. Currently available are:
        * "shift"
        * "rotation"
        * "distortion"

    max_pointing_error : double

        maximum position uncertainty that can be tolerated. If the data exceeds
        this value the astrometric calibration is likely to yield wrong results.

    max_rotator_error : float[2]

        maximum error for the rotator angle.

    """

    logger = logging.getLogger("CCMatch")
    logger.debug("Starting ccmatch")
    logger.debug("max_rot_error: %s degrees" % (str(max_rotator_error)))
    logger.debug("max_pointing_error %s arcmin" % (str(max_pointing_error)))

    # Prepare the structure for the return values
    return_value = {}

    # Convert the max_pointing_error passed as parameter into a list
    try:
        max_pointing_error_list = list(max_pointing_error)
    except TypeError:
        max_pointing_error_list = [max_pointing_error]
    except:
        podi_logging.log_exception()
        logger.critical("I don't understand the format of the max_pointing_error variable, defaulting to fixed 8.0 arcmin")
        max_pointing_error_list = [8.0]
        pass
    
    max_pointing_error_list = numpy.sort(numpy.array(max_pointing_error_list))
    max_pointing_error = numpy.max(max_pointing_error_list)
    return_value['max_pointing_error'] = max_pointing_error
    return_value['max_pointing_error_list'] = max_pointing_error_list

    if (type(source_catalog) == str):
        # Load the source catalog file
        src_catfile = source_catalog
        src_raw = numpy.loadtxt(src_catfile)
    else:
        src_raw = source_catalog

    #
    # Make sure we have a valid input catalog
    #
    if (type(input_hdu) == str):
        # Load the input frame
        logger.debug("optimizing rotation in frame %s" % (input_hdu))
        hdulist = pyfits.open(input_hdu)
    else:
        logger.debug("optimzing WCS in passed HDUlist")
        hdulist = input_hdu


    #
    # compute the center of the field
    #
    center_ra = hdulist[1].header['CRVAL1']
    center_dec = hdulist[1].header['CRVAL2']
    logger.debug("field center at %f   %f" % (center_ra, center_dec))

    # 
    # Create the reference catalog
    #
    if (reference_catalog == None):
        search_size = 0.8 + max_pointing_error/60.
        ref_raw = podi_search_ipprefcat.get_reference_catalog(center_ra, center_dec, search_size, 
                                                              basedir=sitesetup.wcs_ref_dir,
                                                              cattype=sitesetup.wcs_ref_type)
        ref_raw = ref_raw[:,0:2]
    else:
        ref_raw = reference_catalog #numpy.loadtxt(ref_catfile)[:,0:2]
    logger.debug("ref. cat (raw) ="+str(ref_raw.shape))
    
    #
    # Reduce the reference catalog to approx. the coverage of the source catalog
    #
    ref_close = match_catalog_areas(src_raw, ref_raw, max_pointing_error/60.)
    logger.debug("area matched ref. catalog: "+str(ref_close.shape))
    if (ref_close.shape[0] <= 0):
        logger.debug("couldn't find any matched stars overlapping the odi field")
        logger.critical("Found no overlap between 2MASS catalog and ODI source catalog.")
        logger.critical("This should not happen. Please report this problem to the")
        logger.critical("author (kotulla@uwm.edu) and attach the debug.log file. Thanks!")
        logger.debug("Falling back to using the full sample.")

    if (create_debug_files): numpy.savetxt("ccmatch.matched_ref_cat", ref_close)

    # Save the matched 2MASS catalog as return data
    return_value['2mass-catalog'] = ref_close

    
    #
    # eliminate all stars with problematic flags
    #
    flags = numpy.array(src_raw[:,SXcolumn['flags']], dtype=numpy.int8)
    full_src_cat = src_raw[flags == 0]
    logger.debug("src_cat: "+str(full_src_cat.shape))
    if (create_debug_files): numpy.savetxt("ccmatch.src_cat", full_src_cat[:,0:2])


#
    # Eliminate all stars that are just barely detected (mag err > 0.3mag, 
    # equiv to S/N ~ 3) and/or too compact (FWHM < 0.3'') to be "real" stars
    #
    likely_real_stars = (full_src_cat[:,SXcolumn['fwhm_world']] > 0.3/3600.) & \
                        (full_src_cat[:,SXcolumn['mag_err_4.0']] < 0.3)
    full_src_cat = full_src_cat[likely_real_stars]
    if (create_debug_files): numpy.savetxt("ccmatch.src_cat2", full_src_cat[:,0:2])
    logger.debug("Down-selecting source catalog to %d well-detected and not too-compact sources" % (
        full_src_cat.shape[0]))

    #
    # Exclude all stars with nearby neighbors to limit confusion
    #
    only_isolated_stars = True
    if (only_isolated_stars):
        logger.debug("Selecting isolated stars - ODI source catalog")
        if (create_debug_files): numpy.savetxt("ccmatch.odi_full", full_src_cat)
        isolated_stars = pick_isolated_stars(full_src_cat, radius=10)
        if (create_debug_files): numpy.savetxt("ccmatch.odi_isolated", full_src_cat)
        logger.debug("Down-selected source catalog to %d isolated stars" % (full_src_cat.shape[0]))
    else:
        isolated_stars = full_src_cat

    if (isolated_stars.shape[0] <= 0):
        logger.warning("Could not find any isolated stars. This is bad")
        logger.warning("Please tell kotulla@uwm.edu about this")
        isolated_stars = full_src_cat
        logger.debug("Using full catalog instead of only isolated stars")


    #
    # Cut down the catalog size to the brightest n stars
    #
    n_max = 1500 #750
    if (isolated_stars.shape[0] > n_max):
        logger.debug("truncating src_cat:"+str(isolated_stars.shape)+"--> "+str(n_max))
        # That's more than we need, limited the catalog to the brightest n stars
        src_cat, bright_mags = select_brightest(isolated_stars, isolated_stars[:,10:13], n_max)
    else:
        src_cat = isolated_stars

    n_max_ref = 2000
    if (ref_close.shape[0] > n_max_ref):
        logger.debug("Lots of stars (%d) in the reference catalog" % (ref_close.shape[0]))

    ref_cat = ref_close.copy()
    min_distance = 8
    logger.debug("Selecting isolated stars - reference catalog")
    if (create_debug_files): numpy.savetxt("ccmatch.2mass_full", ref_cat)
    while (ref_cat.shape[0] > n_max_ref or min_distance < 10):
        min_distance += 2
        ref_cat = pick_isolated_stars(ref_cat, radius=min_distance)
        if (create_debug_files): numpy.savetxt("ccmatch.2mass_isolated", ref_cat)
        logger.debug("Down-selected reference catalog to %d isolated stars (min_d=%d)" % (ref_cat.shape[0], min_distance))
    logger.debug("Final reference catalog: %d sources, isolated by >%d" % (ref_cat.shape[0], min_distance))

    # 
    # Get rid of all data except the coordinates
    # 
    src_cat = src_cat[:,0:2]


    current_best_rotation = 0
    current_best_shift = [0.,0.]

    if (mode == "shift"):

        # Prepare source catalog
        
        # Load & Prepare reference catalog
        center_ra = hdulist[1].header['CRVAL1']
        center_dec = hdulist[1].header['CRVAL2']

        # Compute the optimal shift vector
        wcs_correction, n_random_matches, contrast = \
            ccmatch_shift(source_cat=src_cat,
                          reference_cat=ref_cat,
                          center=(center_ra, center_dec),
                          pointing_error=(max_pointing_error/60.)
            )
        
        logger.debug(wcs_correction)

        # For testing, apply correction to the input catalog, 
        # match it to the reference catalog and output both to file
        src_rotated = rotate_shift_catalog(src_raw, (center_ra, center_dec), 
                                           angle=0.,
                                           shift=wcs_correction[1:3],
                                           verbose=False)
        matched = kd_match_catalogs(src_rotated, ref_cat, matching_radius=(2./3600.), max_count=1)
        if (create_debug_files): numpy.savetxt("ccmatch.after_shift", matched)

        # src_raw_rotated = rotate_shift_catalog(src_raw, (center_ra, center_dec), 
        #                                        angle=0.,
        #                                        shift=wcs_correction[1:3],
        #                                        verbose=False)

        # Add the best fit shift to outut header to keep track 
        # of the changes we are making
        log_shift_rotation(hdulist, params=wcs_correction, n_step=1,
                           description="WCS calib best guess",
                           n_random_matches=n_random_matches,
                           wcs_contrast = contrast,
        )

        # Now apply this shift to the output file and write results
        apply_correction_to_header(hdulist, wcs_correction, verbose=False)

        # All work is done, write the output FITS file
        # print "writing results ..."
        # hdulist.writeto(outputfile, clobber=True)
        return_value['hdulist'] = hdulist
        return_value['transformation'] = wcs_correction
        return_value['matched_src+2mass'] = matched
        return_value['calibrated_src_cat'] = src_rotated

        return return_value #hdulist, wcs_correction, 

        ##################################################################################
        #
        # End of shift only
        #
        ##################################################################################





    #
    #   |
    #   |
    #   |   This is the end of computing for mode="shift"
    #  \1/  The code below optimizes rotation and possibly distortion
    #   "
    #

    #
    # Find 1st order best guess
    #
    center_ra = hdulist[1].header['CRVAL1']
    center_dec = hdulist[1].header['CRVAL2']

    for pointing_error in max_pointing_error_list:

        logger.debug("Attempting to find WCS solution with search radius %.1f arcmin ..." % (
            max_pointing_error))

        initial_guess, n_random_matches, contrast = \
                find_best_guess(src_cat, ref_cat,
                                center_ra, center_dec,
                                pointing_error=(pointing_error/60.),
                                angle_max=max_rotator_error, #[-2,2], #degrees
                                d_angle=20, # arcmin
                                allow_parallel=True
                )

        logger.debug("Found contrast = %.4f (minimum requirement is %.2f)" % (contrast, min_contrast))

        if (contrast > min_contrast):
            logger.debug("Found good WCS solution (%.2f sigma, search radius: %.2f arcmin)" % (
                contrast, pointing_error))
            break

        return_value['max_pointing_error_searched'] = pointing_error


    if (contrast < min_contrast):
        logger.debug("Failed finding a good enough WCS solution")
        return_value['hdulist'] = hdulist
        return_value['matched_src+2mass'] = None
        return_value['calibrated_src_cat'] = None
        return_value['valid_wcs_solution'] = False
        return return_value

    # Remember to report a valid WCS was found
    return_value['valid_wcs_solution'] = True

    logger = logging.getLogger("CCMatchShift")
    logger.debug("found initial best guess:")
    logger.debug(initial_guess)
    logger.debug("offset = "+str(initial_guess[1:3]*3600.)+" arcsec in Ra/Dec")

    #
    # Add the best fit shift to output header to keep track 
    # of the changes we are making
    #
    log_shift_rotation(hdulist, params=initial_guess, n_step=1,
                       description="WCS initial guess",
                       n_random_matches=n_random_matches,
                       wcs_contrast = contrast,
    )

    #
    # Apply the best guess transformation to the input catalog.
    # use the larger source catalog (only problematic sources removed, no 
    # selection for brightness or isolation) to have more sources for better 
    # results
    #
    current_best_rotation = initial_guess[0]
    current_best_shift = initial_guess[1:3]
    logger.debug("Improving global shift/rotation solution")
    logger.debug("Full ODI source catalog: %d" % (full_src_cat.shape[0]))
    guessed_cat = rotate_shift_catalog(full_src_cat[:,0:2], (center_ra, center_dec), 
                                       angle=current_best_rotation,
                                       shift=current_best_shift,
                                       verbose=False)
    if (create_debug_files): numpy.savetxt("ccmatch.guessed_cat", guessed_cat)

    # 
    # With this best guess at hand, match each star in the source 
    # catalog to a closest match in the reference catalog.
    # Use the full catalog of all close 2MASS stars to have a larger sample
    # 
    logger.debug("2MASS reference stars nearby: %d" % (ref_close.shape[0]))
    crossmatch_radius = 5./3600. # 5 arcsec
    guessed_match = kd_match_catalogs(guessed_cat, ref_close, crossmatch_radius, max_count=1)
    logger.debug("Matched ODI+2MASS: %d" % (guessed_match.shape[0]))
    if (create_debug_files): numpy.savetxt("ccmatch.guessed_match", guessed_match)



    #
    # Now optimize the shift and rotation
    #
    logger.debug("Starting minimization routine to optimize shift & rotation")
    logger.debug("Initial guess: angle = %.5f deg" % (initial_guess[0]))
    logger.debug("Initial guess: dra/ddec = %.7f / %.7f deg [ %.2f / %.2f arcsec]" % (
        initial_guess[1], initial_guess[2], initial_guess[1]*3600., initial_guess[2]*3600.))
    logger.debug("Initial guess: number matches: %d (contrast: %.5f)" % (
        initial_guess[3], contrast))

    best_shift_rotation_solution = fit_best_rotation_shift(
         src_cat, ref_cat, initial_guess,
         center_ra, center_dec,
         matching_radius=(5./3600.)
         )
    # best_shift_rotation_solution = optimize_shift_rotation(guessed_match, hdulist, )
    # print "Alternative method:\n"
    # print "best fit:",best_shift_rotation_solution

    logger.debug("Refined guess: angle = %.5f deg" % (best_shift_rotation_solution[0]))
    logger.debug("Refined guess: dra/ddec = %.7f / %.7f deg [ %.2f / %.2f arcsec]" % (
        best_shift_rotation_solution[1], best_shift_rotation_solution[2], best_shift_rotation_solution[1]*3600., best_shift_rotation_solution[2]*3600.))

    src_rotated = rotate_shift_catalog(full_src_cat, (center_ra, center_dec), 
                                       angle=best_shift_rotation_solution[0],
                                       shift=best_shift_rotation_solution[1:3],
                                       verbose=False)
    matched = kd_match_catalogs(src_rotated, ref_close, matching_radius=(2./3600.), max_count=1)
    if (create_debug_files): numpy.savetxt("ccmatch.after_shift+rot", matched)


    current_best_rotation = best_shift_rotation_solution[0]
    current_best_shift = best_shift_rotation_solution[1:3]
    n_matches = numpy.sum(numpy.isfinite(matched[:,2]))

    # Add the refined shift and rotation to output header to keep track 
    # of the changes we are making
    log_shift_rotation(hdulist, params=best_shift_rotation_solution, n_step=2, 
                       description="WCS rot refi",
    )

    logger.debug("Writing shift/rotation to output file")
    for ext in range(len(hdulist)):
        if (not is_image_extension(hdulist[ext])):
            continue
        ota_extension = hdulist[ext]
        wcs_poly = header_to_polynomial(ota_extension.header)
        wcs_poly = wcs_apply_rotation(wcs_poly, current_best_rotation)
        wcs_poly = wcs_apply_shift(wcs_poly, current_best_shift)
        wcs_wcspoly_to_header(wcs_poly, ota_extension.header)

    # For testing, apply correction to the input catalog, 
    # match it to the reference catalog and output both to file
    src_rotated = rotate_shift_catalog(src_raw, (center_ra, center_dec), 
                                       angle=current_best_rotation,
                                       shift=current_best_shift,
                                       verbose=False)
    matched = kd_match_catalogs(src_rotated, ref_close, matching_radius=(2./3600.), max_count=1)
    if (create_debug_files): numpy.savetxt("ccmatch.after_rotation", matched)

    # We only asked for rotation optimization, so 
    # end the processing right here
    if (mode == "rotation"):
        return_value['hdulist'] = hdulist
        return_value['transformation'] = best_shift_rotation_solution
        return_value['matched_src+2mass'] = matched
        return_value['calibrated_src_cat'] = src_rotated

        logger.debug("All done here, returning")
        return return_value 

    #newcat = recompute_radec_from_xy(hdulist, src_rotated)
    #numpy.savetxt("ccmatch.newcat-afterrot", newcat)
    #matched_newcat = kd_match_catalogs(newcat, ref_close, matching_radius=(2./3600.), max_count=1)
    #numpy.savetxt("ccmatch.newcat-afterrot2", matched_newcat)
    

    #
    #   |
    #   |     All code below is allowing a OTA-level optimization.
    #   |     Proceed with caution !!
    #  \1/
    #   "

    #
    # First, most simple step: Refine the location of each OTA to account
    # for some large-scale distortion
    logger.debug("Optimizing each OTA separately, shift only (ODI: %d, 2MASS: %d)" % (
        src_rotated.shape[0], ref_close.shape[0]))
    global_cat, hdulist, matched_global = \
        improve_wcs_solution(src_rotated, 
                             ref_close,
                             hdulist,
                             headers_to_optimize=(
                                 'CRVAL1', 'CRVAL2',
                             ),
                             matching_radius=(3./3600),
                             min_ota_catalog_size=4,
                             output_catalog = "ccmatch.after_otashift",
                         )


    if (mode == "otashift"):
        return_value['hdulist'] = hdulist
        return_value['transformation'] = best_shift_rotation_solution
        return_value['matched_src+2mass'] = matched_global
        return_value['calibrated_src_cat'] = global_cat
    
        logger.debug("All done here, returning")
        return return_value 


    #
    # Next refinement step, allow for smaller scale distortion by allowing for 
    # shear in the CD matrix
    #
    logger.debug("Optimizing each OTA separately, shift+shear (ODI: %d, 2MASS: %d)" % (
        global_cat.shape[0], ref_close.shape[0]))
    global_cat, hdulist, matched_global = \
        improve_wcs_solution(global_cat, 
                             ref_close,
                             hdulist,
                             headers_to_optimize=(
                                 'CRVAL1', 'CRVAL2',
                                 'CD1_1', 'CD1_2', 'CD2_1', 'CD2_2'
                             ),
                             matching_radius=(3./3600),
                             min_ota_catalog_size=9,
                             output_catalog = "ccmatch.after_shear2",
                         )

    if (mode == "otashear"):
        return_value['hdulist'] = hdulist
        return_value['transformation'] = best_shift_rotation_solution
        return_value['matched_src+2mass'] = matched_global
        return_value['calibrated_src_cat'] = global_cat
    
        logger.debug("All done here, returning")
        return return_value 


    #
    #   |
    #   |     All code below is to optimize
    #   |     the distortion within the frame
    #   |     Proceed with caution !!
    #  \1/
    #   "

    
    # 
    # Now we have the best fitting rotation and shift position.
    # In a next step, go ahead and refine the distortion in the frame
    # 
    print "\n\n\nOptimizing distortion..."
    print "Using initial guess", best_shift_rotation_solution

    # Apply the best-fit rotation to all coordinates in source catalog
    # src_raw = numpy.loadtxt(src_catfile)
    full_src_cat = src_raw.copy()
    if (create_debug_files): numpy.savetxt("ccmatch.opt_src", full_src_cat)
    
    src_rotated = rotate_shift_catalog(full_src_cat[:,0:2], (center_ra, center_dec), 
                                       angle=current_best_rotation,
                                       shift=current_best_shift,
                                       verbose=True)

    if (create_debug_files): numpy.savetxt("ccmatch.opt_rot", src_rotated)

    # Update the Ra/Dec in the full source catalog
    full_src_cat[:,0:2] = src_rotated[:,0:2]

    # Now we have the full source catalog, with better matching star coordinates
    # Next, eliminate all stars with flags
    valid_stars = full_src_cat[:,7] == 0
    valid_src_cat = full_src_cat[valid_stars]

    if (create_debug_files): numpy.savetxt("ccmatch.opt_ref", ref_cat)
    if (create_debug_files): numpy.savetxt("ccmatch.opt_valid", valid_src_cat)

    # diff = full_src_cat[:,0:2] - src_raw[:,0:2]
    # diff2 = src_rotated[:,0:2] - src_raw[:,0:2]
    # numpy.savetxt("ccmatch.opt_diff", diff)
    # numpy.savetxt("ccmatch.opt_diff2", diff2)


    #
    # Next we can do the actual coordinate matching between the source and 
    # reference star catalog
    #
    matched_catalog = kd_match_catalogs(valid_src_cat, ref_cat, matching_radius=(2./3600))
    if (create_debug_files): numpy.savetxt("ccmatch.match_cat_for_distopt", matched_catalog)
    
    print "OTAs of each source:\n",matched_catalog[:,8]

    for ext in range(len(hdulist)):
        if (not is_image_extension(hdulist[ext])):
            continue

        ota_extension = hdulist[ext]
        # print ota_extension.header['FPPOS'], ota_extension.header['FPPOS'][2:4]
        ota = int(ota_extension.header['FPPOS'][2:4])
        print "\n\n\nworking on OTA %02d ..." %(ota)

        # sources from this OTA
        in_this_ota = (matched_catalog[:,8] == ota)
        print numpy.sum(in_this_ota)
        number_src_in_this_ota = numpy.sum(in_this_ota)

        # Read the WCS imformation from the fits file
        wcs_poly = header_to_polynomial(ota_extension.header)

        # And apply the best results for shift and rotation
        wcs_poly = wcs_apply_rotation(wcs_poly, best_shift_rotation_solution[0])
        wcs_poly = wcs_apply_shift(wcs_poly, best_shift_rotation_solution[1:3])


        if (number_src_in_this_ota < 15):
            print "Not enough stars to optimize distortion"
            wcs_poly_after_fit = wcs_poly

        else:

            # print in_this_ota
            # print matched_catalog[:,8]

            ota_cat = matched_catalog[in_this_ota]
            ota_ref = matched_catalog[in_this_ota][:,-2:] #31:33]

            print "sources in ota %d = %s ..." % (ota, str(ota_cat.shape))

            xi, xi_r, eta, eta_r, cd, crval, crpix = wcs_poly
            #numpy.savetxt(sys.stdout, xi, "%9.2e")
            #numpy.savetxt(sys.stdout, xi_r, "%9.2e")

            # wcs_poly = update_polynomial(wcs_poly, 
            #                              numpy.array([1.11, 2.22, 3.33, 4.44]), 
            #                              numpy.array([1.11, 2.22, 3.33, 4.44]), 
            #                              )

            # Read starting values from current WCS solution
            wcs_poly_to_arrays(wcs_poly)

            #
            # Now with the updated header, compute ra,dec from x,y
            #
            ra_dec = wcs_pix2wcs(ota_cat[:,2:4], wcs_poly)

            if (create_debug_files): numpy.savetxt("ccmatch.true_radec.OTA%02d" % (ota), ota_cat[:,0:2])
            if (create_debug_files): numpy.savetxt("ccmatch.computed_radec.OTA%02d" % (ota), ra_dec)

            xi, xi_r, eta, eta_r, cd, crval, crpix = wcs_poly
            xi[0,0] = 10000
            wcs_poly2 = xi, xi_r, eta, eta_r, cd, crval, crpix
            ra_dec2 = wcs_pix2wcs_2(ota_cat[:,2:4], wcs_poly2)
            if (create_debug_files): numpy.savetxt("ccmatch.computed_radec2.OTA%02d" % (ota), ra_dec2)

            # sys.exit(0)

            xi, xi_r, eta, eta_r, cd, crval, crpix = wcs_poly
            print
            #numpy.savetxt(sys.stdout, xi, "%9.2e")
            #numpy.savetxt(sys.stdout, xi_r, "%9.2e")

            def optimize_distortion(p, input_xy, input_ref, wcs_poly, fit=True):
                n_params = p.shape[0] / 2
                p_xi = p[:n_params]
                p_eta = p[-n_params:]

                wcs_poly_for_fit = update_polynomial(wcs_poly, p_xi, p_eta)

                ra_dec_computed = wcs_pix2wcs(input_xy, wcs_poly_for_fit)
                diff = input_ref - ra_dec_computed
                if (not fit):
                    return diff
                print p_xi,p_eta,numpy.sum(diff**2)
                return diff.ravel()

            #
            # Determine initial guesses from the current wcs distortion
            #
            xi_1d, eta_1d = wcs_poly_to_arrays(wcs_poly)

            # For now, let's optimize only the first 4 factors
            n_free_parameters = 3 # or 4 or 7 or 12 or 17 or 24
            p_init = numpy.append(xi_1d[:n_free_parameters], eta_1d[:n_free_parameters])
            print p_init

            print "ota-cat=\n",ota_cat[:,2:4]
            print "ota-ref=\n",ota_ref

            diff = optimize_distortion(p_init, ota_cat[:,2:4], ota_ref, wcs_poly, fit=False)
            if (create_debug_files): numpy.savetxt("ccmatch.optimize_distortion_before_OTA%02d" % (ota), diff)

            if (True):
                print "\n\n\n\n\n\n\nStarting fitting\n\n\n\n\n"
                args = (ota_cat[:,2:4], ota_ref, wcs_poly, True)
                fit = scipy.optimize.leastsq(optimize_distortion, 
                                             p_init, 
                                             args=args, 
                                             full_output=1)

                print "\n\n\n\n\n\n\nDone with fitting"
                print p_init
                print fit[0]
                print "\n\n\n\n\n"
                p_afterfit = fit[0]
            else:
                p_afterfit = p_init

            diff_after = optimize_distortion(p_afterfit, ota_cat[:,2:4], ota_ref, wcs_poly, fit=False)
            if (create_debug_files): numpy.savetxt("ccmatch.optimize_distortion_after_OTA%02d" % (ota), diff_after)
        
            wcs_poly_after_fit = update_polynomial(wcs_poly, 
                                                   p_afterfit[:n_free_parameters],
                                                   p_afterfit[-n_free_parameters:]
                                                   )

        #
        # Make sure to write the updated WCS header back to the HDU
        # This is done even if not enough sources for distortion fitting were found
        #
        wcs_wcspoly_to_header(wcs_poly_after_fit, ota_extension.header)

    #print "writing results ..."
    #hduout = pyfits.HDUList(hdulist[0:3])
    #hduout.append(hdulist[13])
    #hduout.writeto(outputfile, clobber=True)
    # hdulist.writeto(outputfile, clobber=True)
    return hdulist



def global_wcs_quality(odi_2mass_matched, ota_list):

    wcs_quality = {}
    logger = logging.getLogger("ComputeWCSQuality")

    # compute global values
    logger.debug("Global:")
    q = compute_wcs_quality(odi_2mass_matched, ota_list[0].header)
    wcs_quality['full'] = q

    for ext in range(0, len(ota_list)):
        if (not is_image_extension(ota_list[ext])):
            continue

        extname = ota_list[ext].header['EXTNAME']
        ota = ota_list[ext].header['OTA']
        in_this_ota = odi_2mass_matched[:,8] == ota

        matched_ota = odi_2mass_matched[in_this_ota]

        logger.debug(extname)
        q = compute_wcs_quality(matched_ota, ota_list[ext].header)
        wcs_quality[extname] = q

    return wcs_quality


def compute_wcs_quality(odi_2mass_matched, hdr=None):

    logger = logging.getLogger("ComputeWCSQuality")

    d_dec = (odi_2mass_matched[:,1] - odi_2mass_matched[:,-1])
    d_ra  = ((odi_2mass_matched[:,0] - odi_2mass_matched[:,-2]) 
             * numpy.cos(numpy.radians(odi_2mass_matched[:,1])))

    if (create_debug_files): numpy.savetxt("wcsquality.test", odi_2mass_matched)
    d_total = numpy.hypot(d_ra, d_dec)
    wcs_scatter = numpy.median(d_total)
    wcs_scatter2 = numpy.std(d_total)
    wcs_mean_dra = numpy.median(d_ra) * 3600.
    wcs_mean_ddec = numpy.median(d_dec) * 3600.
    rms_dra = numpy.sqrt(numpy.mean(d_ra**2)) * 3600.
    rms_ddec = numpy.sqrt(numpy.mean(d_dec**2)) * 3600.
    rms_comb = numpy.sqrt(numpy.mean(d_dec**2+d_ra**2)) * 3600.

    try:
        lsig_ra = scipy.stats.scoreatpercentile(d_ra, 16)
        hsig_ra = scipy.stats.scoreatpercentile(d_ra, 84)
        sigma_ra = 0.5 * (hsig_ra - lsig_ra) * 3600.
        lsig_dec = scipy.stats.scoreatpercentile(d_dec, 16)
        hsig_dec = scipy.stats.scoreatpercentile(d_dec, 84)
        sigma_dec = 0.5 * (hsig_dec - lsig_dec) * 3600.
        lsig_total = scipy.stats.scoreatpercentile(d_total, 16)
        hsig_total = scipy.stats.scoreatpercentile(d_total, 84)
        sigma_total = 0.5 * (hsig_total - lsig_total) * 3600.
    except:
        sigma_ra, sigma_dec, sigma_total = -99, -99, -99
        pass

    def make_valid(x):
        return x if numpy.isfinite(x) else -9999

    results = {}
    results['RMS-RA'] = rms_dra
    results['RMS-DEC'] = rms_ddec
    results['RMS'] = rms_comb #numpy.hypot(rms_dra, rms_ddec)
    results['SIGMA-RA'] = sigma_ra
    results['SIGMA-DEC'] = sigma_dec
    results['SIGMA'] = sigma_total #numpy.hypot(rms_dra, rms_ddec)
    results['MEDIAN-RA'] = wcs_mean_dra
    results['MEDIAN-DEC'] = wcs_mean_ddec
    results['STARCOUNT'] = d_ra.shape[0]

    logger.debug("WCS quality: mean-offset=%(MEDIAN-RA).3f  , %(MEDIAN-DEC).3f [arcsec]" % results)
    logger.debug("WCS quality: mean-rms=%(RMS-RA).3f , %(RMS-DEC).3f , %(RMS).3f [arcsec]" % results)
    logger.debug("WCS quality: sigma=%(SIGMA-RA).3f , %(SIGMA-DEC).3f , %(SIGMA).3f [arcsec]" % results)
    # print "WCS quality:", ota, wcs_mean_dra*3600., wcs_mean_ddec*3600., wcs_scatter*3600., wcs_scatter2*3600., rms_dra, rms_ddec
    
    if (not hdr == None):
        hdr["WCS_RMSA"] = (make_valid(results['RMS-RA']),     "RA r.m.s. of WCS matching [arcsec]")
        hdr["WCS_RMSD"] = (make_valid(results['RMS-DEC']),    "DEC r.m.s. of WCS matching [arcsec]")
        hdr["WCS_RMS"] =  (make_valid(results['RMS']),        "r.m.s. of WCS matching [arcsec]")
        hdr["WCS_ERRA"] = (make_valid(results['MEDIAN-RA']),  "RA median error WCS matching [arcsec]")
        hdr["WCS_ERRD"] = (make_valid(results['MEDIAN-DEC']), "DEC median error of WCS matching [arcsec]")
        hdr["WCS_NSRC"] = (results['STARCOUNT'],              "number of sources for WCS calibration")
        hdr["WCS_SIGA"] = (sigma_ra,                          "1-sigma width of WCS error in Ra")
        hdr["WCS_SIGD"] = (sigma_dec,                         "1-sigma width of WCS error in Dec")
        hdr["WCS_SIG"]  = (sigma_total,                       "1-sigma width of WCS error combined")
 
    return results


if __name__ == "__main__":
    verbose=False

    if (cmdline_arg_isset('-isolate')):
        source_catalog = get_clean_cmdline()[1]
        radius = float(get_clean_cmdline()[2])
        catalog = numpy.loadtxt(source_catalog)
        isolated = pick_isolated_stars(catalog, radius=radius)
        numpy.savetxt(source_catalog+".isolated", isolated)
        sys.exit(0)

    elif (cmdline_arg_isset('-verify')):
        source_catalog = numpy.loadtxt(get_clean_cmdline()[1])
        input_hdu = get_clean_cmdline()[2]
        hdulist = pyfits.open(input_hdu)

        verify_wcs_model(source_catalog, hdulist)

    elif (cmdline_arg_isset("-testmatch")):

        import podi_plotting
        import matplotlib.pyplot

        ra = float(get_clean_cmdline()[1])
        dec = float(get_clean_cmdline()[2])
        width = float(get_clean_cmdline()[3])
        overlap = float(get_clean_cmdline()[4])

        ref_raw = podi_search_ipprefcat.get_reference_catalog(ra, dec, width, 
                                                              basedir=sitesetup.wcs_ref_dir,
                                                              cattype=sitesetup.wcs_ref_type)
        numpy.savetxt("2mass_test.raw", ref_raw)

        # Now create a fake-catalog of only the 4 corners
        src_cat = numpy.array([
            [ra, dec],
            [ra-width, dec-width],
            [ra+width, dec-width],
            [ra+width, dec+width],
            [ra-width, dec+width],
        ])

        ref_close = match_catalog_areas(src_cat, ref_raw, overlap/60.)
        numpy.savetxt("2mass_test.match", ref_close)

        fig = matplotlib.pyplot.figure()
        ax = fig.add_subplot(111)
        ax.scatter(ref_raw[:,0], ref_raw[:,1], c="grey", marker="+")
        ax.scatter(ref_close[:,0], ref_close[:,1], c="blue", marker="o", linewidths=0)
        ax.set_xlim((ra-width, ra+width))
        ax.set_ylim((dec-width, dec+width))
        fig.savefig("2mass_test.png")

    elif (cmdline_arg_isset("-applynonsidereal")):

        if (not cmdline_arg_isset("-nonsidereal")):
            print "The -nonsidereal flag wasn't given, don't know what to do"
        else:
            # open the input file
            input_file = get_clean_cmdline()[1]
            ota_list = pyfits.open(input_file)
            import podi_collectcells
            options = podi_collectcells.read_options_from_commandline()

            # Apply the non-sidereal option
            apply_nonsidereal_correction(ota_list, options)

            output_file = get_clean_cmdline()[2]
            ota_list.writeto(output_file, clobber=True)
        
    elif (cmdline_arg_isset("-debug")):
        
        options = set_default_options()
        podi_logging.setup_logging(options)

        fitsfile = get_clean_cmdline()[1]
        catfile = get_clean_cmdline()[2]
        hdu_list = pyfits.open(fitsfile)
        src_cat = numpy.loadtxt(catfile)

        ccmatched = ccmatch(source_catalog=src_cat,
                            reference_catalog=None, # meaning ccmtch will obtain it
                            input_hdu=hdu_list, 
                            mode="otashear",
                            max_rotator_error=[0,3],
                        )

        if (len(get_clean_cmdline()) > 3):
            outfile = get_clean_cmdline()[3]
            hdu_list.writeto(outfile, clobber=True)

        podi_logging.shutdown_logging(options)

    else:
        mode = cmdline_arg_set_or_default('-mode', 'xxx')
        print mode

        valid_modes = (
            "shift",
            "rotation",
            "otashift",
            "otashear",
            "distortion"
        )
        # valid_mode = (mode == "shift" or mode == "rotation" or mode == "distortion")
        if (not mode in valid_modes):
            print "This mode is not known"
            print "valid modes are:",valid_modes

#             print """\
# This mode is not known.
# Valid modes are only
#   * shift
#   * rotation
#   * distortion
# """
            sys.exit(0)

        source_catalog = get_clean_cmdline()[1]
    
        reference_catalog = None
    
        input_hdu = get_clean_cmdline()[2]

        ccmatched = ccmatch(source_catalog, reference_catalog, input_hdu, mode)

        output_hdu = ccmatched['hdulist']
        outputfile = get_clean_cmdline()[3]
    
        output_hdu.writeto(outputfile, clobber=True)
