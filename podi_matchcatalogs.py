#!/usr/bin/env python


import sys
import numpy
import os
from podi_definitions import *


arcsec = 1./3600.

def match_catalogs(ref_full, odi_full, matching_radius=2, verbose=False):

    ref_ra_min, ref_ra_max = numpy.min(ref_full[:,0]), numpy.max(ref_full[:,0])
    ref_dec_min, ref_dec_max = numpy.min(ref_full[:,1]), numpy.max(ref_full[:,1])

    dummy, ra_ranges = numpy.histogram([], bins=5, range=(ref_ra_min, ref_ra_max))
    dummy, dec_ranges = numpy.histogram([], bins=5, range=(ref_dec_min, ref_dec_max))

    matched_cat = None
    for cur_ra in range(ra_ranges.shape[0]-1):
        for cur_dec in range(dec_ranges.shape[0]-1):

            # Select one of the ranges and hand the parameters off to the matching routine
            matched_cat_segment = match_catalog_segment(ref_full, odi_full, 
                                                        ra_ranges[cur_ra:cur_ra+2], 
                                                        dec_ranges[cur_dec:cur_dec+2],
                                                        matching_radius=matching_radius,
                                                        verbose=verbose)
            
            if (matched_cat_segment == None):
                continue

            matched_cat = matched_cat_segment if (matched_cat == None) \
                else numpy.append(matched_cat, matched_cat_segment, axis=0)

    return matched_cat

def match_catalog_segment(ref_full, odi_full, ra_ranges, dec_ranges, 
                          matching_radius=2, verbose=False):

    # First of all extract only the reference stars in the selected box
    ref_select = (ref_full[:,0] >  ra_ranges[0]) & (ref_full[:,0] <=  ra_ranges[1]) \
               & (ref_full[:,1] > dec_ranges[0]) & (ref_full[:,1] <= dec_ranges[1])

    ref = ref_full[ref_select]

    if (ref.shape[0] <= 0):
        return None

    # Now do the same, just with ranges extended by the matching radius / cos(dec)
    max_cos_dec = numpy.max(numpy.fabs(ref[:,1]))
    d_ra  = matching_radius * arcsec * math.cos(math.radians(max_cos_dec))
    d_dec = matching_radius * arcsec
    
    odi_select = (odi_full[:,0] >  ra_ranges[0]-d_ra ) & (odi_full[:,0] <  ra_ranges[1]+d_ra ) \
               & (odi_full[:,1] > dec_ranges[0]-d_dec) & (odi_full[:,1] < dec_ranges[1]+d_dec)
    odi = odi_full[odi_select]

    if (verbose):
        print "Searching for reference stars: RA: %3.1f - %3.1f,  DEC: %+4.1f - %+4.1f" % (
            ra_ranges[0]-d_ra, ra_ranges[1]+d_ra,dec_ranges[0]-d_dec,dec_ranges[1]+d_dec),

    # Create the output array, and set all ODI position to the "not found" standard value
    output_array = numpy.zeros(shape=(ref.shape[0], ref.shape[1]+odi.shape[1]))
    output_array[:, 2:4] = -99999.9

    # Copy all values from the reference catalog
    odi_data_start = 4 + ref.shape[1] - 2 
    output_array[:, 0:2] = ref[:, 0:2]
    output_array[:, 4:odi_data_start] = ref[:, 2:]

    matches_found = 0
    if (odi.shape[0] > 0):
        #
        # Now for each star, find the closest match in the reference catalog
        #
        matching_radius = 2 * arcsec
        matching_radius_squared = matching_radius * matching_radius
        for star in range(ref.shape[0]):

            d_ra  = (ref[star,0] - odi[:,0]) * math.cos(math.radians(ref[star,1]))
            d_dec = (ref[star,1] - odi[:,1])
            sq_d = d_ra * d_ra + d_dec * d_dec

            si = numpy.argsort(sq_d)
            if (sq_d[si[0]] < matching_radius_squared):
                # The closest match is a valid one, with distance < matching_radius
                # Copy the ODI coordinates and catalog data into the output format
                output_array[star, 2:4] = odi[si[0], 0:2]
                output_array[star, odi_data_start:] = odi[si[0], 2:]
                matches_found += 1

    if (verbose):
        print "  -->    %5d matches (%5d vs %5d)" % (matches_found, ref.shape[0], odi.shape[0])
    
    return output_array

    
if __name__ == "__main__":

    print "Hello"
