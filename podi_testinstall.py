#!/usr/bin/env python3
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


"""

podi_testinstall is a small tool that checks if all package dependencies at met.

See the podi-website at http://members.galev.org/rkotulla/research/podi-pipeline
for a full list of currently required packages.

"""

import subprocess
import sys, os, shutil
import multiprocessing
import datetime


def check_package(name):
    """
    Try to import a package and print feedback message for the user whether or
    not the package has been found.
    """

    try:
        import_cmd = "import %s as pkg" % (name)
        exec(import_cmd)

    except ImportError as e:
        print("\nProblem importing %s :\n %s" % (name, str(e)))
    except:
        print("\nSome error occured while trying to import %s" % (name))
    else:
        try:
            version = "??" #pkg.__version__
            print("Found package: %s (version: %s)" % (name, version))
        except AttributeError:
            print("Found package: %s" % (name))
            pass
        return True

    return False

def ask_for_option(var_name, question, backup, needs_quotes, config_array, grab_only=False):

    import podi_sitesetup

    suggestion = backup
    if (hasattr(podi_sitesetup, var_name)):
        suggestion = eval('podi_sitesetup.%s' % (var_name))
        changed = False
    else:
        changed = True

    if (grab_only):
        changed = False
        answer = suggestion

    else:

        # Now ask user
        print("----")
        print(question)
        print("Default:", suggestion)
        try:
            answer = input("Answer:  ")
        except KeyboardInterrupt:
            print("\nTerminating\n")
            pass
            sys.exit(0)

        if (len(answer.strip()) <= 0):
            answer = suggestion
    
    # New configuration string
    if (needs_quotes):
        config_string = "%s = \"%s\"" % (var_name, answer)
    else:
        config_string = "%s = %s" % (var_name, answer)

    config_array.append(config_string)

    return (changed or (not answer == suggestion))

def update_sitesetup(change_setup=True, change_catalogs=True, grab_only=False):

    try:
        import podi_sitesetup
        if (not grab_only):
            print("********************************************************")
            print("**                                                    **")
            print("** This is an update, loading existing configuration  **")
            print("**                                                    **")
            print("********************************************************")
            print()
        else:
            print("Keeping existing configuration")
    except:
        print("********************************************************")
        print("**                                                    **")
        print("** This is the very first run, initializing sitesetup **")
        print("**                                                    **")
        print("********************************************************")
        print()
        shutil.copy("podi_sitesetup.py.blank", "podi_sitesetup.py")
        import podi_sitesetup
        pass

    
    # Now go over all options, ask user for input
    # Use the current configuration as default value
    
    config_array = ["# Your setup parameters are next:"]

    changes = False
    changes = changes | ask_for_option('max_cpu_count', 
                   "Maximum number of CPU cores available for use in reduction", 
                   multiprocessing.cpu_count(), False, config_array, grab_only=grab_only)
    changes = changes | ask_for_option('wcs_ref_dir', 
                   "Directory holding the local 2MASS catalog in FITS format", 
                   "/some/dir", True, config_array, grab_only=grab_only)
    changes = changes | ask_for_option('wcs_ref_type', 
                   "Type of astrometric reference catalog (leave unchanged)", 
                   "2mass_nir", True, config_array, grab_only=grab_only)

    changes = changes | ask_for_option('sdss_ref_type', 
                   "Source of SDSS catalog (choose from local, web, stripe82)", 
                   "local", True, config_array, grab_only=grab_only)
    changes = changes | ask_for_option('sdss_ref_dir', 
                   "If local SDSS catalog, specify source directory", 
                   "/some/dir", True, config_array, grab_only=grab_only)

    changes = changes | ask_for_option('ucac4_ref_dir', 
                   "Directory of the UCAC4 directory - specify none if it does not exist", 
                   "none", True, config_array, grab_only=grab_only)

    changes = changes | ask_for_option('ippref_ref_dir', 
                   "Directory of the IPPRef directory - specify none if it does not exist", 
                   "none", True, config_array, grab_only=grab_only)

    changes = changes | ask_for_option('scratch_dir', 
                   "Scratch-directory for temporary files (the faster the better)", 
                   "/tmp", True, config_array, grab_only=grab_only)

    changes = changes | ask_for_option('sextractor', 
                   "Path to SourceExtractor executable (Hint: run 'which sex' in another terminal)", 
                   "/usr/local/bin/sex", True, config_array, grab_only=grab_only)
    changes = changes | ask_for_option('sex_redirect', 
                   "Command to re-direct stdout and stderr to /dev/null", 
                   " >& /dev/null", True, config_array, grab_only=grab_only)
    changes = changes | ask_for_option('sex_delete_tmps', 
                   "Delete SourceExtractor temporary files after use (choose from True (recommended) or False)", 
                   "True", False, config_array, grab_only=grab_only)

    changes = changes | ask_for_option('diagplot__zeropoint_ZPrange', 
                   "Vertical range around median in photometric calibation plots (format: [0.3,0.3]", 
                   "[0.5,0.5]", False, config_array, grab_only=grab_only)
    changes = changes | ask_for_option('diagplot__zeropoint_magrange', 
                   "SDSS Magnitude range in photometric calibation plots (format: [11, 22]", 
                   "[11, 21]", False, config_array, grab_only=grab_only)
    changes = changes | ask_for_option('diagplot__zeropointmap_range', 
                   "Spread around median ZP in photometric zeropoint map (format: [0.3,0.3]", 
                   "[0.2,0.2]", False, config_array, grab_only=grab_only)

    changes = changes | ask_for_option('debug_log_filename', 
                   "Filename for debug logs (e.g. /tmp/debug.log) ", 
                   "debug.log", True, config_array, grab_only=grab_only)
    changes = changes | ask_for_option('debug_log_append', 
                   "Keep adding to debug-log (choose True) or create a new file for each run (False)", 
                   "True", False, config_array, grab_only=grab_only)

    changes = changes | ask_for_option('crj_sigclip', 
                   "Cosmic Ray rejection: Sigma-clipping threshold", 
                   5.0, False, config_array, grab_only=grab_only)
    changes = changes | ask_for_option('crj_sigfrac', 
                   "Cosmic Ray rejection: Fraction of sigma-clipping for nearby neighbors", 
                   0.3, False, config_array, grab_only=grab_only)
    changes = changes | ask_for_option('crj_objlim', 
                   "Cosmic Ray rejection: Minimum CR significance above sources", 
                   5.0, False, config_array, grab_only=grab_only)
    changes = changes | ask_for_option('crj_saturation', 
                   "Cosmic Ray rejection: Saturation limit (negative disables this feature)", 
                   55000, False, config_array, grab_only=grab_only)
    changes = changes | ask_for_option('crj_niter', 
                   "Cosmic Ray rejection: Number of CR rejection iterations (typically in the range 1-4)", 
                   4, False, config_array, grab_only=grab_only)

    changes = changes | ask_for_option('swarp_exec', 
                   "Path to Swarp executable (Hint: run 'which swarp' in another terminal)", 
                   "swarp", True, config_array, grab_only=grab_only)
    changes = changes | ask_for_option('swarp_singledir', 
                   "Path for swarp intermediate files (you'll need several GB here!)", 
                   ".", True, config_array, grab_only=grab_only)

    changes = changes | ask_for_option('exec_dir', 
                   "Path where all the podi-scripts are installed", 
                   os.path.abspath("."), True, config_array, grab_only=grab_only)
    
    changes = changes | ask_for_option('fixwcs_mode', 
                   "FixWCS mode: shift, rotation, otashift, otashear, distortion; otashear is recommended", 
                   "otashear", True, config_array, grab_only=grab_only)
    changes = changes | ask_for_option('max_pointing_error', 
                   "Maximum allowed pointing error for astrometric calibration", 
                   [2,5,8,12,20], False, config_array, grab_only=grab_only)
    changes = changes | ask_for_option('max_rotator_error', 
                   "Maximum allowed range for rotator error for astrometric calibration", 
                   [-3,3.5], False, config_array, grab_only=grab_only)
    changes = changes | ask_for_option('min_wcs_quality', 
                   "Minimum WCS quality for successful calibration", 
                   3.0, False, config_array, grab_only=grab_only)
    changes = changes | ask_for_option('wcs_match_multiplier', 
                   "FixWCS: Match-multiplier", 
                   0.6, False, config_array, grab_only=grab_only)

    changes = changes | ask_for_option('saturation_limit', 
                   "Saturation count rate; pixel >= X are considered saturated", 
                   65535, False, config_array, grab_only=grab_only)
    changes = changes | ask_for_option('persistency_duration', 
                   "Maximum timescale that persistency affects the data in seconds", 
                   600, False, config_array, grab_only=grab_only)

    changes = changes | ask_for_option('photcalib_saturation',
                   "Max peak flux for stars used for photometric calibration",
                   53000, False, config_array, grab_only=grab_only)

    changes = changes | ask_for_option('log_shell_output', 
                   "Log output from external programs (e.g. SourceExtrator, SWarp) in debug log [True/False]?", 
                   False, False, config_array, grab_only=grab_only)
    
    changes = changes | ask_for_option('staging_dir', 
                   "Directory to hold staged/cached input files", 
                   "/tmp/", True, config_array, grab_only=grab_only)
    changes = changes | ask_for_option('sextractor_cache_dir', 
                   "Directory to hold temporary files for use by sextractor", 
                   "/tmp/", True, config_array, grab_only=grab_only)

    changes = changes | ask_for_option('flat_order', 
                   "Order of flat-fields to use", 
                   ['tflat', 'dflat', 'flat'], False, config_array, grab_only=grab_only)

    changes = changes | ask_for_option('per_ota_timeout', 
                   "Emergency timeout to prevent a stalled program when a child-process dies", 
                   300, False, config_array, grab_only=grab_only)
    
    changes = changes | ask_for_option('mastercal_cache',
                   "Directory to hold local, WIYN-delivered mastercal products",
                   "/tmp", True, config_array)

    config_array.append("")

    # print config_array

    return config_array, changes



def configure_catalogs(catalog_config=None):

    #
    # Now configure all catalogs
    #
    try:
        import podi_sitesetup
    except:
        shutil.copy("podi_sitesetup.py.blank", "podi_sitesetup.py")
        import podi_sitesetup
        pass

    if (catalog_config is None):
        catalog_config = []

    catalogs_changed = False

    #ordered = OrderedDict


    while (True):

        print("\n\nCurrently available source catalogs:")
        if (podi_sitesetup.catalog_directory == {}):
            print(" XXX There are no catalogs configured yet!")
        else:
            for catalogname in podi_sitesetup.catalog_directory:
                cat_dir, cat_magcol = podi_sitesetup.catalog_directory[catalogname]
                print(" ** % 12s --> %s [%d]" % (catalogname, cat_dir, cat_magcol))

        print

        try:
            answer = input("Add [a], change [c] or delete [d] catalog, or quit [q]?")
        except KeyboardInterrupt:
            print("\nTerminating\n")
            sys.exit(0)

        if (answer.lower() in ['a', 'c']):
            while (True):
                cat_name = input("name [no spaces!]:")
                if (cat_name == ""):
                    print("No empty catalog name allowed")
                    continue
                break

            while (True):
                cat_dir = input("Catalog directory:")
                # Add some verification here
                if (cat_dir.lower() == "q"):
                    cat_dir = None
                    break
                elif (not os.path.isdir(cat_dir)):
                    print("The specified directory [%s] does not exist" % (cat_dir))
                    continue
                elif (not os.path.isfile("%s/SkyTable.fits" % cat_dir)):
                    print("The specified directory does not contain a valid Index file")
                    continue
                else:
                    break

            if (cat_dir is not None):
                while(True):
                    try:
                        cat_mag = int(input("Column of first magnitude [must be int]:"))
                        break
                    except ValueError:
                        continue
                print(cat_name, cat_dir, cat_mag)

                podi_sitesetup.catalog_directory[cat_name] = (cat_dir, cat_mag)
                catalogs_changed = True
        elif (answer.lower() == 'd'):
            cat_name = input("catalog name:")
            if (cat_name in podi_sitesetup.catalog_directory):
                del podi_sitesetup.catalog_directory[cat_name]
                catalogs_changed = True
            else:
                print("Catalog name not found!")
            continue

        elif (answer.lower() == 'q'):
            break

    #
    # Now that we have the full configuration, prepare the block of code to add to the sitesetup
    #
    for cat_name in podi_sitesetup.catalog_directory:
        (cat_dir, cat_mag) = podi_sitesetup.catalog_directory[cat_name]
        catalog_config.append("catalog_directory['%s'] = (\"%s\",%d)" % (cat_name, cat_dir, cat_mag))

    # print catalogs_changed
    # print catalog_config

    return catalog_config, catalogs_changed

def check_component(pkg, fct):
    found = hasattr(pkg, fct)
    print("   * %-20s: %s" % (fct, found))
    return found

if __name__ == "__main__":
    print()
    print("Testing if all packages are installed")
    print()

    print("\nchecking standard packages ...")
    check_package('os')
    check_package('sys')
    check_package('math')
    check_package('time')
    check_package('types')
    check_package('ctypes')
    check_package('itertools')

    print("\nchecking multi-processor packages ...")
    check_package('multiprocessing')
    check_package('Queue')
    check_package('threading')
    check_package('subprocess')
    check_package('psutil')


    print("\nchecking numerical processing packages ...")
    check_package('numpy')
    check_package('scipy')
    check_package('scipy.stats')
    check_package('scipy.optimize')
    check_package('scipy.interpolate')
    check_package('scipy.ndimage')
    check_package('bottleneck')

    print("\nchecking plotting packages ...")
    check_package('matplotlib')
    check_package('Image')
    check_package('ImageDraw')


    print("\nchecking astronomy-related packages ...")
    check_package('pyfits')
    check_package('ephem')
    check_package('astLib')
    check_package('jdcal')

    if (not check_package('podi_sitesetup')):
        print("""\
        Module podi_sitesetup is a global configuration file for
        this podi pipeline. Copy the existing file
        podi_sitesetup.py.example to podi_sitesetup.py, open it
        in a text-editor and make sure the global settings for the 
        WCS and photometric reference catalogs are set correctly.
        Then re-run this program.

    """)

    print("\nChecking cython-optimized package for pODI")
    done_checking_cython = False
    while (not done_checking_cython):
        if (not check_package('podi_cython')):
            print("""\
 There was a problem import podi_cython. This module contains optimized
 code that needs to be compiled first. To do so, simply run:

 python3 setup.py build_ext --inplace

""")
             
            try:
                answer = input("Do you want to compile this module now? (y/n)")
            except KeyboardInterrupt:
                print("\nTerminating\n")
                sys.exit(0)
            if (answer in ['y', 'Y']):
                subprocess.call("python3 setup.py build_ext --inplace".split())
                continue
            else:
                break
        else:
            # print "Checking podi_cython components:"
            import podi_cython
            all_found = True
            all_found = all_found and check_component(podi_cython, "sigma_clip_mean")
            all_found = all_found and check_component(podi_cython, "sigma_clip_median")
            all_found = all_found and check_component(podi_cython, "lacosmics")
            if (all_found):
                print("All routines found")
                done_checking_cython = True
                break
            else:
                print("Some podi-cython routines could not be found!")
                print("Please re-compile via python setup.py build_ext --python")
    
    print("\nCheck done!\n")

    answer = input("Do you want to run the sitesetup assistant (y/N)?")
    config_array = []
    config_changed = False
    if (answer.lower() == "y"):
        print("\n"*4,"     Starting auto-configuration!","\n"*4)
        # import sys, os, podi_sitesetup
        config_array, config_changed = update_sitesetup()
    else:
        # create config_array from existing configuration
        config_array, changes = update_sitesetup(grab_only=True)

    answer = input("Do you want to run the catalog setup assistant (y/N)?")
    catalog_config = ['', '', '# Catalog configuration']
    catalogs_changed = False
    if (answer.lower() == "y"):
        print("\n"*4,"     Starting catalog-configuration!","\n"*4)
        # import sys, os, podi_sitesetup
        catalog_config, catalogs_changed = configure_catalogs(catalog_config)
    else:
        # create catalog configuration from existing catalog information in podi_sitesetup
        import podi_sitesetup
        for cat_name in podi_sitesetup.catalog_directory:
            cat_dir, cat_mag = podi_sitesetup.catalog_directory[cat_name]
            catalog_config.append("catalog_directory['%s'] = (\"%s\", %d)" % (cat_name, cat_dir, cat_mag))

    #
    # Now update sitesetup
    #
    blank_setup = open("podi_sitesetup.py.blank", "r")
    lines = blank_setup.readlines()
    # Now find where to insert variables
    insert_at = -1
    for i in range(len(lines)):
        # print "____%s___" % (lines[i].strip())
        if (lines[i].startswith("###AUTO-CONFIG-INSERT-HERE")):
            # print "Found insert point"
            insert_at = i
            break

    if (config_changed or catalogs_changed):
        print("\n\nThere were some changes to the configuration!")
        while (True):
            answer = input("Are you sure you want to update the configuration (y/n)? ")
            if (len(answer) > 0):
                break
            else:
                print("I really need an answer!",)
        if (answer == "y" or answer == "Y"):
            backup_file = "podi_sitesetup.py.backup_from_%s" % (datetime.datetime.now().strftime("%Y%m%dT%H%M%S"))
            # os.system("cp podi_sitesetup.py %s" % (backup_file))
            try:
                shutil.copy("podi_sitesetup.py", backup_file)
            except:
                pass

            # print config_array
            # print catalog_config

            new_config = open("podi_sitesetup.py", "w")
            new_config.write("".join(lines[:insert_at]))
            new_config.write(os.linesep.join(config_array))
            new_config.write(os.linesep.join(catalog_config))
            new_config.write(os.linesep*3)
            new_config.write("".join(lines[insert_at + 1:]))
            new_config.close()
            print("Changes saved!")
        else:
            print("Keeping configuration unchanged")
    else:
        print("\nNo changes found, keeping current configuration")

