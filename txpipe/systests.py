from ceci import PipelineStage
from .data_types import MetacalCatalog, YamlFile, HDFFile, TomographyCatalog, SysTestResults
import stile
import pandas
import numpy as np

class TXSysTests(PipelineStage):
    """
    A stage to perform a variety of systematic tests.
    """
    name='TXSysTests'

    inputs = [
        ('shear_catalog', MetacalCatalog),
        ('tomography_catalog', TomographyCatalog),
        ('photometry_catalog', HDFFile),
    ]
    outputs = [
        ('systest_results', SysTestResults)
    ]

    config_options = {
        'tests': ['Rho1','histogram'],
        'stat_vars': ['g1','g2']
    }

    def run(self):
        """
        running this stage."""
        import numpy as np

        self.get_requiredcols()
        data = self.load_shearcatalog()

        stile_args = {'ra_units': 'degrees', 'dec_units': 'degrees',
               'min_sep': 0.05, 'max_sep': 1, 'sep_units': 'degrees', 'nbins': 20}

        for test in self.config['tests']:
            if test=='histogram':
                for var in self.config['stat_vars']:
                    hist = stile.HistogramSysTest()
                    histplot = hist.HistoPlot(data[var][0:10000],binning_style='scott',histtype='bar',align='left')
                    histplot.savefig(var+'hist_test')
            if test=='stats':
                for var in self.config['stat_vars']:
                    print(stile.StatSysTest(var))
            if test=='Rho1':
                rho1_test = stile.CorrelationFunctionSysTest(test)
                rho1_result = rho1_test(data,config=stile_args)
                rho1_plot = rho1_test.plot(rho1_result)
                rho1_plot.savefig('rho1.png')
        #output_file = self.open_output('systest_results')
        #self.write_output('systest_results')

    def load_shearcatalog(self):
        """Store the data in a recarry with Stile's column
        name convention. TODO adapt this be general to all columns.
        """
        cols = self.required_columns
        #print('cols',cols)

        #Now get the required data from the metacal shear_catalog
        #This is rough, needs improvement
        metacal_cols = []
        for var in cols:
            if var=='x':
                print('No camera coordinates yet.')
            elif var=='y':
                print('No camera coordinates yet. ')
            if var=='g1' or 'g2':
                metacal_cols.append('mcal_g')
            if var=='psf_g1' or 'psf_g2':
                metacal_cols.append('mcal_gpsf')
            if var=='w':
                continue
            if var!='x' and var!='y' and var!='g1' and var!='g2' and var!='psf_g1' and var!='psf_g2' and var!='w':
                metacal_cols.append(var)

        print('mcal_cols',metacal_cols)

        f = self.open_input('shear_catalog')
        data = f[1].read_columns(metacal_cols)

        mcal_g1 = data['mcal_g'][:,0]
        mcal_g2 = data['mcal_g'][:,1]

        mcal_psfg1 = data['mcal_gpsf'][:,0]
        mcal_psfg2 = data['mcal_gpsf'][:,1]

        data_rec = np.zeros(len(data['ra']), dtype={'names':('ra','dec','g1','g2','psf_g1','psf_g2','w'),
                          'formats':('f','f','f','f','f','f','f')})
        data_rec['ra'] = data['ra']
        data_rec['dec'] = data['dec']
        data_rec['g1'] = mcal_g1
        data_rec['g2'] = mcal_g2
        data_rec['psf_g1'] = mcal_psfg1
        data_rec['psf_g2'] = mcal_psfg2
        data_rec['w'] = np.ones(len(mcal_g1))
        return data_rec

    def get_requiredcols(self):
        """Get the required column names for the test.

        - Return a numpy recarry in Stile convention.
        """
        cols = []
        for test in self.config['tests']:
            #For general statistics tests record that variable
            if test=='histogram':
                for var in self.config['stat_vars']:
                    cols.append(var)
            elif test=='stats':
                for var in self.config['stat_vars']:
                    cols.append(var)
            #For specialized tests record the required quantities
            else:
                test = stile.CorrelationFunctionSysTest(test)
                for var in test.required_quantities[0]:
                    cols.append(var)
        self.required_columns = cols
