from ..base_stage import PipelineStage
from ..data_types import HDFFile, PickleFile, NOfZFile, PNGFile
from ..photoz_stack import Stack
import numpy as np

class PZRailSummarize(PipelineStage):
    name = "PZRailSummarize"
    inputs = [
        ("tomography_catalog", HDFFile),
        ("photometry_catalog", HDFFile),
        ("model", PickleFile),

    ]
    outputs = [
        # TODO: Change to using QP files throughout
        ("photoz_stack", NOfZFile),
        ("photoz_realizations", HDFFile),
    ]

    # pull these out automatically
    config_options = {
        "mag_prefix": "photometry/mag_",
        "tomography_name": str,
    }

    def run(self):
        import rail.estimation
        import pickle
        from rail.estimation.algos.NZDir import NZDir
        from rail.core import DataStore, TableHandle
        import tables_io

        model_filename = self.get_input(self.get_aliased_tag("model"))

        # The usual way of opening pickle files puts a bunch
        # of provenance at the start of them. External pickle files
        # like the ones from RAIL don't have this, so the file content
        # comes out wrong.
        # Once we've moved the provenance stuff from TXPipe to ceci
        # then prov tracking should be harmonized, then we can replace
        # these two lines:
        with open(model_filename, "rb") as f:
            model = pickle.load(f)
        # with these:
        # with self.open_input("nz_dir_model", wrapper=True) as f:
        #     model = f.read()


        bands = model["szusecols"]
        prefix = self.config["mag_prefix"]

        # This is the bit that will not work with realistically sized
        # data sets. Need the RAIL parallel interface when ready.
        with self.open_input("photometry_catalog") as f:
            full_data = {b: f[f"{prefix}{b}"][:] for b in bands}

        tomo_name = self.config["tomography_name"]
        with self.open_input("tomography_catalog") as f:
            g = f['tomography']
            nbin = g.attrs[f'nbin_{tomo_name}']
            bins = g[f'{tomo_name}_bin'][:]


        # Generate the configuration for RAIL. Anything set in the
        # config.yml file will also be put in here by the bit below,
        # so we don't need to try all possible RAIL options.
        sub_config = {
            "model": model,
            "usecols": bands,
            "hdf5_groupname": "",
            "phot_weightcol":"",
            "output_mode": "none",  # actually anything except "default" will work here
            "comm": self.comm,
        }

        # TODO: Make this flexible
        substage_class = NZDir

        for k, v in self.config.items():
            if k in substage_class.config_options:
                sub_config[k] = v


        # Just do things with the first bin to begin with
        qp_per_bin = []
        realizations_per_bin = {}

        for i in range(nbin):

            # Extract the chunk of the data assigned to this tomographic
            # bin.
            index = (bins==i)
            nobj = index.sum()
            data = {b: full_data[b][index] for b in bands}
            print(f"Computing n(z) for bin {i}: {nobj} objects")

            # Store this data set. Once this is parallelised will presumably have to delete
            # it afterwards to avoid running out of memory.
            data_handle = substage_class.data_store.add_data(f"tomo_bin_{i}", data, TableHandle)
            substage = substage_class.make_stage(name=f"NZDir_{i}", **sub_config)
            substage.set_data('input', data_handle)
            substage.run()

            if self.rank == 0:
                realizations_per_bin[f'bin_{i}'] = substage.get_handle('output').data
                bin_qp = substage.get_handle('single_NZ').data
                qp_per_bin.append(bin_qp)

        # now we do the 2D case
        index = bins >= 0
        nobj = index.sum()
        data = {b: full_data[b][index] for b in bands}
        print(f"Computing n(z) for bin {i}: {nobj} objects")

        # Store this data set. Once this is parallelised will presumably have to delete
        # it afterwards to avoid running out of memory.
        data_handle = substage_class.data_store.add_data(f"tomo_bin_2d", data, TableHandle)
        substage = substage_class.make_stage(name=f"NZDir_2d", **sub_config)
        substage.set_data('input', data_handle)
        substage.run()
        if self.rank == 0:
            realizations_2d = substage.get_handle('output').data
            qp_2d = substage.get_handle('single_NZ').data

        if self.rank > 0:
            return

        # TODO: Convert to just saving QP files. Might need to fix metadata saving.
        # Also, this currently assumes that the histogram form of the QP ensemble
        # is used, which may not always be true.

        # All the tomo bins should have the same z values
        # so copy out the first one's values here
        t = qp_per_bin[0].build_tables()

        # The stack class just wants the lower edges, and
        # the bins value has shape (1, nbin) here.
        z = t['meta']['bins'][0, :-1]
        nz = len(z)
        stack = Stack(tomo_name, z, nbin)

        # Go through each bin setting n(z) on our object directly.
        for i,q in enumerate(qp_per_bin):
            t = q.build_tables()
            stack.set_bin(i, t['data']['pdfs'][0])



        stack_2d = Stack(tomo_name + "2d", z, 1)
        t = qp_2d.build_tables()
        stack_2d.set_bin(0, t['data']['pdfs'][0])
            
        # Save final stack
        with self.open_output("photoz_stack") as f:
            stack.save(f)
            stack_2d.save(f)


        # Save the realizations
        with self.open_output("photoz_realizations") as f:
            group = f.create_group("realizations")
            npdf = realizations_per_bin["bin_0"].npdf

            group.attrs["nbin"] = nbin
            group.attrs["nz"] = nz
            group.attrs["nreal"] = npdf

            # Collect all the PDFs as a single 3D array
            pdfs = np.empty((npdf, nbin, nz))
            for i in range(nbin):
                ensemble = realizations_per_bin[f"bin_{i}"]
                for j in range(npdf):
                    pdfs[j, i] = ensemble[j].objdata()["pdfs"]
                
            group.create_dataset("pdfs", data=pdfs)
            group.create_dataset("z", data=z)

            pdfs_2d = np.empty((npdf, nz))
            for j in range(npdf):
                pdfs_2d[j] = realizations_2d[j].objdata()["pdfs"]

            group.create_dataset("pdfs_2d", data=pdfs_2d)

                
class PZRealizationsPlot(PipelineStage):
    name = "PZRealizationsPlot"

    inputs = [
        ("photoz_realizations", HDFFile),
    ]

    outputs = [
        ("photoz_realizations_plot", PNGFile),
    ]

    config_options = {
        "zmax": -1.0,
    }

    def run(self):
        import h5py
        import matplotlib.pyplot as plt

        with self.open_input("photoz_realizations") as f:
            pdfs = f["/realizations/pdfs"][:]
            z = f["/realizations/z"][:]

        nreal, nbin, nz = pdfs.shape
        alpha = 1.0 / nreal

        # if there are more than 1000 of these then don't bother plotting them all
        nreal = min(nreal, 1000)

        with self.open_output("photoz_realizations_plot", wrapper=True) as fig:
            ax = fig.file.subplots()
            for b in range(nbin):
                line, = ax.plot(z, pdfs[0, b], alpha=0.2)
                color = line.get_color()
                for i in range(1, nreal):
                    ax.plot(z, pdfs[i, b], alpha=0.2, color=color)
            if self.config["zmax"] > 0:
                ax.set_xlim(0, self.config["zmax"])
            ax.set_ylim(0, None)
            ax.set_xlabel("z")
            ax.set_ylabel("n(z)")
