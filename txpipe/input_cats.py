from ceci import PipelineStage
from descformats.tx import MetacalCatalog, HDFFile

# could also just load /global/projecta/projectdirs/lsst/groups/CS/descqa/catalog/ANL_AlphaQ_v3.0.hdf5


class TXDProtoDC2Mock(PipelineStage):
    """


    """
    name='TXDProtoDC2Mock'
    inputs = [
    ]
    outputs = [
        ('metacal_catalog', MetacalCatalog),
        ('photometry_catalog', HDFFile),
    ]
    config_options = {'cat_name':'protoDC2_test', 'visits_per_band':165, 'snr_limit':4.0}

    def data_iterator(self, gc):

        cols = ['mag_true_u_lsst', 'mag_true_g_lsst', 
                'mag_true_r_lsst', 'mag_true_i_lsst', 
                'mag_true_z_lsst',
                'ra', 'dec',
                'ellipticity_1_true', 'ellipticity_2_true',
                'shear_1', 'shear_2',
                'size_true',
                'galaxy_id',
                ]

        it = gc.get_quantities(cols, return_iterator=True)
        for data in it:
            yield data

    def get_catalog_size(self, gc):
        import h5py
        filename = gc.get_catalog_info()['filename']
        print(f"Reading catalog size directly from {filename}")
        f = h5py.File(filename)
        n = f['galaxyProperties/ra'].size
        f.close()
        return n

    def run(self):
        import GCRCatalogs
        cat_name = self.config['cat_name']
        self.bands = ('u','g', 'r', 'i', 'z')

        gc = GCRCatalogs.load_catalog(cat_name)
        N = self.get_catalog_size(gc)

        metacal_file = self.open_output('metacal_catalog', clobber=True)
        photo_file = self.open_output('photometry_catalog', parallel=False)

        # This is the kind of thing that should go into
        # the DESCFormats stuff
        self.setup_photometry_output(photo_file, N)
        self.setup_metacal_output(metacal_file, N)

        self.current_index = 0
        for data in self.data_iterator(gc):
            mock_photometry = self.make_mock_photometry(data)
            mock_metacal = self.make_mock_metacal(data, mock_photometry)
            self.remove_undetected(mock_photometry, mock_metacal)
            self.write_photometry(photo_file, mock_photometry)
            self.write_metacal(metacal_file, mock_metacal)

            
        # Tidy up
        self.truncate_photometry(photo_file)
        photo_file.close()
        metacal_file.close()


    def setup_photometry_output(self, photo_file, N):
        # Get a list of all the column names
        cols = ['ra', 'dec']
        for band in self.bands:
            cols.append(f'mag_true_{band}_lsst')
            cols.append(f'true_snr_{band}')
            cols.append(f'snr_{band}')
            cols.append(f'mag_{band}_lsst')

        # Make group for all the photometry
        group = photo_file.create_group('photometry')

        # Extensible columns becase we don't know the size yet.
        # We will cut down the size at the end.
        for col in cols:
            group.create_dataset(col, (N,), maxshape=(N,), dtype='f8')

        # The only non-float column for now
        group.create_dataset('galaxy_id', (N,), maxshape=(N,), dtype='i8')
    
    def setup_metacal_output(self, metacal_file, N):
        print("Should setup metacal output here")
        

    def write_photometry(self, photo_file, mock_photometry):
        # Work out the range of data to output (since we will be
        # doing this in chunks)
        start = self.current_index
        n = len(mock_photometry['galaxy_id'])
        end = start + n
        
        # Save each column
        for name, col in mock_photometry.items():
            photo_file[f'photometry/{name}'][start:end] = col

        # Update starting point for next round
        self.current_index += n

    def write_metacal(self, metacal_file, mock_metacal):
        print("Should save metacal data here")

    def make_mock_photometry(self, data):
        # The visit count affects the overall noise levels
        n_visit = self.config['visits_per_band']
        # Do all the work in the function below
        photo = make_mock_photometry(n_visit, self.bands, data)
        return photo



    def make_mock_metacal(self, data, photo):
        """
        Generate a mock metacal table with noise added
        """

        # TODO: Write
        print("Should make metacal mock here too")
        return None

        # These are the numbers from figure F1 of the DES Y1 shear catalog paper
        # (this version is not yet public but is awaiting a second referee response)
        import numpy as np
        import scipy.interpolate

        # strategy here.
        # we have a S/N per band for each object.
        # get the total S/N in r,i,z since most shapes are measured there.
        # if it's > 5 then make a fake R_mean value based on the signal to noise and size
        # Then generate R_mean from R with some spread
        # 
        # Just use the true shear * R_mean for the estimated shear
        # (the noise on R will do the job of noise on shear)
        # Use R11 = R22 and R12 = R21 = 0

        # Overall SNR for the three bands usually used
        snr = (photo[f'snr_r']**2 + photo[f'snr_i'] + photo[f'snr_z'])**0.5

        # wasteful - we are making this every chunk of data
        spline_snr = np.log10([0.01,  5.7,   7.4,   9.7,  12.6,  16.5,  21.5,  28. ,  36.5,  47.5,
                    61.9,  80.7, 105.2, 137.1, 178.7, 232.9, 303.6, 395.7, 515.7,
                    672.1, 875.9])
        spline_R = array([0.001,  0.07, 0.15, 0.25, 0.35, 0.43, 0.5 , 0.54, 0.56, 0.58, 0.59, 0.59,
                    0.6 , 0.6 , 0.59, 0.57, 0.55, 0.52, 0.5 , 0.48, 0.46])

        spline = scipy.interpolate.interp1d(spline_snr, spline_R, kind='cubic')

        # Now we need the SNR of the object.

        # TODO: Setup metacal catalog 

    def remove_undetected(self, photo, metacal):
        import numpy as np
        snr_limit = self.config['snr_limit']
        detected = False

        # Check if detected in any band.  Makes a boolean array
        # Even though we started with just a single False.
        for band in self.bands:
            detected_in_band = photo[f'snr_{band}'] > snr_limit
            not_detected_in_band = ~detected_in_band
            # Set objects not detected in one band that are detected in another
            # to inf magnitude in that band, and the SNR to zero.
            photo[f'snr_{band}'][not_detected_in_band] = 0.0
            photo[f'mag_{band}_lsst'][not_detected_in_band] = np.inf

            # Record that we have detected this object at all
            detected |= detected_in_band


        # Remove all objects not detected in *any* band
        # make a copy of the keys with photo.keys so we are not
        # modifying during the iteration
        for key in list(photo.keys()): 
            photo[key] = photo[key][detected]

        # TODO: cut metacal too
        print("Should cut metacal here too")

    def truncate_photometry(self, photo_file):
        group = photo_file['photometry']
        cols = list(group.keys())
        for col in cols:
            group[col].resize((self.current_index,))
            

def make_mock_photometry(n_visit, bands, data):
    """
    Generate a mock photometric table with noise added

    This is mostly from LSE‐40 by 
    Zeljko Ivezic, Lynne Jones, and Robert Lupton
    retrieved here:
    http://faculty.washington.edu/ivezic/Teaching/Astr511/LSST_SNRdoc.pdf
    """

    import numpy as np

    output = {}
    output['ra'] = data['ra']
    output['dec'] = data['dec']
    output['galaxy_id'] = data['galaxy_id']


    # Sky background, seeing FWHM, and system throughput, 
    # all from table 2 of Ivezic, Jones, & Lupton
    B_b = np.array([85.07, 467.9, 1085.2, 1800.3, 2775.7])
    fwhm = np.array([0.77, 0.73, 0.70, 0.67, 0.65])
    T_b = np.array([0.0379, 0.1493, 0.1386, 0.1198, 0.0838])


    # effective pixels size for a Gaussian PSF, from equation
    # 27 of Ivezic, Jones, & Lupton
    pixel = 0.2 # arcsec
    N_eff = 2.436 * (fwhm/pixel)**2


    # other numbers from Ivezic, Jones, & Lupton
    sigma_inst2 = 10.0**2  #instrumental noise in photons per pixel, just below eq 42
    gain = 1  # ADU units per photon, also just below eq 42
    D = 6.5 # primary mirror diameter in meters, from LSST key numbers page (effective clear diameter)
    # Not sure what effective clear diameter means but it's closer to the number used in the paper
    time = 30. # seconds per exposure, from LSST key numbers page
    sigma_b2 = 0.0 # error on background, just above eq 42

    # combination of these  used below, from various equations
    factor = 5455./gain * (D/6.5)**2 * (time/30.)

    for band, b_b, t_b, n_eff in zip(bands, B_b, T_b, N_eff):
        # truth magnitude
        mag = data[f'mag_true_{band}_lsst']

        # expected signal photons, over all visits
        c_b = factor * 10**(0.4*(25-mag)) * t_b * n_visit

        # expected background photons, over all visits
        background = np.sqrt((b_b + sigma_inst2 + sigma_b2) * n_eff * n_visit)
        # total expected photons
        mu = c_b + background
        
        # Observed number of photons in excess of the expected background.
        # This can go negative for faint magnitudes, indicating that the object is
        # not going to be detected
        n_obs = np.random.poisson(mu) - background

        # signal to noise, true and estimated values
        true_snr = c_b / background
        obs_snr = n_obs / background

        # observed magnitude from inverting c_b expression above
        mag_obs = 25 - 2.5*np.log10(n_obs/factor/t_b/n_visit)

        output[f'true_snr_{band}'] = true_snr
        output[f'snr_{band}'] = obs_snr
        output[f'mag_{band}_lsst'] = mag_obs

    return output



def test():
    import numpy as np
    import pylab
    data = {
        'ra':None,
        'dec':None,
        'galaxy_id':None,
    }
    bands = ('u','g','r','i','z')
    n_visit=165
    M5 = [24.22, 25.17, 24.74, 24.38, 23.80]
    for b,m5 in zip(bands, M5):
        data[f'mag_true_{b}_lsst'] = np.repeat(m5, 10000)
    results = make_mock_photometry(n_visit, bands, data)
    pylab.hist(results['snr_r'], bins=50, histtype='step')
    pylab.savefig('snr_r.png')
