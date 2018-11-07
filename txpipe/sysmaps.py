from ceci import PipelineStage
from .data_types import MetacalCatalog, TomographyCatalog, DiagnosticMaps, HDFFile
import numpy as np

class TXDiagnosticMaps(PipelineStage):
    """
    For now, this Pipeline Stage computes a depth map using the DR1 method,
    which takes the mean magnitude of objects close to 5-sigma S/N.

    In the future we will add the calculation of other diagnostic maps
    like airmass for use in systematics tests and covariance mode projection.

    DM may in the future provide tools we can use in place of the methods
    used here, but not on the DC2 timescale.


    """
    name='TXDiagnosticMaps'

    # We currently take everything from the shear catalog.
    # In the long run this may become DM output
    inputs = [
        ('photometry_catalog', HDFFile),
        ('shear_catalog', MetacalCatalog),
        ('tomography_catalog', TomographyCatalog),
    ]

    # We generate a single HDF file in this stage
    # containing all the maps
    outputs = [
        ('diagnostic_maps', DiagnosticMaps),
    ]

    # Configuration information for this stage
    config_options = {
        'pixelization': 'healpix', # The pixelization scheme to use, currently just healpix
        'nside':0,   # The Healpix resolution parameter for the generated maps. Only req'd if using healpix
        'snr_threshold':float,  # The S/N value to generate maps for (e.g. 5 for 5-sigma depth)
        'snr_delta':1.0,  # The range threshold +/- delta is used for finding objects at the boundary
        'chunk_rows':100000,  # The number of rows to read in each chunk of data at a time
        'sparse':True,   # Whether to generate sparse maps - faster and less memory for small sky areas,
        'ra_cent':np.nan,  # These parameters are only required if pixelization==tan
        'dec_cent':np.nan,  
        'npix_x':-1, 
        'npix_y':-1, 
        'pixel_size':np.nan, # Pixel size of pixelization scheme
        'depth_band' : 'i',
    }


    def run(self):
        """
        Run the analysis for this stage.

         - choose the pixelization scheme for the map
         - loop through chunks of the photometry catalog (in paralllel if enabled)
         - build up the map gradually
         - the master process saves the map
        """
        from .mapping import DepthMapperDR1
        from .utils import choose_pixelization
        import numpy as np

        # Read input configuration informatiomn
        config = self.config


        # Select a pixelization scheme based in configuration keys.
        # Looks at "pixelization as the main thing"
        pixel_scheme = choose_pixelization(**config)
        config.update(pixel_scheme.metadata)

        chunk_rows = config['chunk_rows']
        band = config['depth_band']

        # These are the columns we're going to need from the various files
        phot_cols = ['ra', 'dec', f'snr_{band}', f'mag_{band}_lsst']
        shear_cols = ['mcal_g']
        bin_cols = ['source_bin', 'lens_bin']
        m_cols = ['R_gamma']


        source_bins = list(range(nbin_source))
        lens_bins = list(range(nbin_lens))


        # Make two mapper classes, one for the signal itself
        # (shear and galaxy count) and the other for the depth
        # calculation
        mapper = Mapper(pixel_scheme, lens_bins, source_bins)
        depth_mapper = DepthMapperDR1(pixel_scheme,
                                      config['snr_threshold'],
                                      config['snr_delta'],
                                      sparse = config['sparse'],
                                      comm = self.comm)


        # Build some "iterators".  Iterators are things you can loop through,
        # but the good thing here is that they don't get created all at once
        # (which here would mean loading in a great number of data columns)
        # but instead are "lazy" - at this point all that happens is that
        # we prepare to read these columns from these different files.
        # These methods by default yield trios of (start, end, data),
        # but in this case because we are agregating we don't need the "start" and
        # "end" numbers.  So we re-define to ignore them
        shear_it = self.iterate_fits('shear_catalog', 1, shear_cols, chunk_rows)
        shear_it = (d[2] for d in shear_it)

        phot_it = self.iterate_hdf('photometry_catalog', 'photometry', phot_cols, chunk_rows)
        phot_it = (d[2] for d in phot_it)

        bin_it = self.iterate_hdf('tomography_catalog','tomography', bin_cols, chunk_rows)
        bin_it = (d[2] for d in bin_it)
                
        m_it = self.iterate_hdf('tomography_catalog','multiplicative_bias', m_cols, chunk_rows)
        m_it = (d[2] for d in m_it)

        # Now, we actually start loading the data in.
        # This thing below just loops through all the files at once
        iterator = zip(shear_it, phot_it, bin_it, m_it)
        for shear_data, phot_data, bin_data, m_data in iterator:

            # Pick out a few relevant columns from the different
            # files to give to the depth mapper.
            depth_data = {
                'mag': phot_data[f'mag_{band}_lsst'],
                'snr': phot_data[f'snr_{band}'],
                'bins': bin_data['lens_bin'],
            }

            # And add these data chunks to our maps
            depth_mapper.add_data(depth_data)
            mapper.add_data(shear_data, bin_data, m_data)

        # Collect together the results across all the processors
        # and combine them to get the final results
        depth_pix, depth_count, depth, depth_var = depth_mapper.finalize(self.comm)
        pixel, ngals, g1, g2 = mapper.finalize(self.comm)


        
        # Only the root process saves the output
        if self.rank==0:
            # Open the HDF5 output file
            outfile = self.open_output('diagnostic_maps')
            # Use one global section for all the maps
            group = outfile.create_group("maps")
            # Save each of the maps in a separate subsection
            self.save_map(group, "depth", depth_pix, depth, config)
            self.save_map(group, "depth_count", depth_pix, depth_count, config)
            self.save_map(group, "depth_var", depth_pix, depth_var, config)

            # I'm expecting this will one day call off to a 10,000 line
            # library or something.
            mask, npix = self.compute_mask(count)
            self.save_map(group, "mask", depth_pix, mask, config)

            # Save some other handy map info that will be useful later
            area = pixel_scheme.pixel_area(degrees=True) * npix
            group.attrs['area'] = area
            group.attrs['area_unit'] = 'sq deg'

            # Now save all the lens bin galaxy counts, under the
            # name ngal
            for b in lens_bins:
                self.save_map(group, f"ngal_{b}", map_pix, ngals[b])

            for b in source_bins:
                self.save_map(group, f"g1_{b}", map_pix, g1[b])
                self.save_map(group, f"g2_{b}", map_pix, g2[b])


    def compute_mask(self, depth_count):
        mask = np.zeros_like(depth_count)
        hit = depth_count > 0
        mask[hit] = 1.0
        count = hit.sum()
        return mask, count



    def save_map(self, group, name, pixel, value, metadata):
        """
        Save an output map to an HDF5 subgroup.

        The pixel numbering and the metadata are also saved.

        Parameters
        ----------

        group: H5Group
            The h5py Group object in which to store maps
        name: str
            The name of this map, used as the name of a subgroup in the group where the data is stored.
        pixel: array
            Array of indices of observed pixels
        value: array
            Array of values of observed pixels
        metadata: mapping
            Dict or other mapping of metadata to store along with the map
        """
        subgroup = group.create_group(name)
        subgroup.attrs.update(metadata)
        subgroup.create_dataset("pixel", data=pixel)
        subgroup.create_dataset("value", data=value)






if __name__ == '__main__':
    PipelineStage.main()
