from obspy.core import UTCDateTime
import numpy as np
from rtree import index
from glob import glob
from mpi4py import MPI
from collections import defaultdict
from obspy import read
from obspy.core import Stream, Trace
import os
from tqdm import tqdm

MAX_DATE = UTCDateTime(4102444800.0)
MIN_DATE = UTCDateTime(-2208988800.0)

def rtp2xyz(r, theta, phi):
    xout = np.zeros((r.shape[0], 3))
    rst = r * np.sin(theta)
    xout[:, 0] = rst * np.cos(phi)
    xout[:, 1] = rst * np.sin(phi)
    xout[:, 2] = r * np.cos(theta)
    return xout
# end func

class MseedIndex:
    def split_list(lst, npartitions):
        k, m = divmod(len(lst), npartitions)
        return [lst[i * k + min(i, m):(i + 1) * k + min(i + 1, m)] for i in range(npartitions)]
    # end func

    def __init__(self, mseed_folder, pattern):
        self.mseed_folder = mseed_folder
        self.comm = MPI.COMM_WORLD
        self.nproc = self.comm.Get_size()
        self.rank = self.comm.Get_rank()
        self.tree = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(list))))

        work_load = None
        offsets = None
        if (self.rank == 0):
            self.mseed_files = np.array(glob(os.path.join(self.mseed_folder, pattern)))
            work_load = MseedIndex.split_list(self.mseed_files, self.nproc)
            counts = np.array([len(item) for item in work_load])
            offsets = np.append(0, np.cumsum(counts[:-1]))
        # end if
        self.local_mseed_files = self.comm.scatter(work_load)
        self.offsets = self.comm.bcast(offsets, root=0)

        print('Reading metadata from mseed files..')
        meta_list = []
        for i, mseed_file in enumerate(tqdm(self.local_mseed_files, desc='Rank {}'.format(self.rank))):
            st = None
            try:
                st = read(mseed_file, headonly=True)
            except:
                print("Failed to read {}. Moving along..". format(mseed_file))
                continue
            # end try

            for tr in st:
                nc, sc, lc, cc, st, et = \
                    tr.stats.network, tr.stats.station, tr.stats.location, \
                    tr.stats.channel, tr.stats.starttime.timestamp, \
                    tr.stats.endtime.timestamp

                meta_list.append([self.offsets[self.rank] + i, nc, sc, lc, cc, st, et])
            # end for
            #if (i > 0): break
        # end for

        meta_list = self.comm.gather(meta_list, root=0)
        if (self.rank == 0):
            print('Creating metadata index..')

            meta_list = [item for ritem in meta_list for item in ritem]  # flatten list of lists

            for row in tqdm(meta_list):
                idx, nc, sc, lc, cc, st, et = row

                if (type(self.tree[nc][sc][lc][cc]) != index.Index):
                    self.tree[nc][sc][lc][cc] = index.Index()
                # end if
                self.tree[nc][sc][lc][cc].insert(idx, (st, 1, et, 1))
            # end for
        # end if
    # end func

    def get_waveforms(self, net, sta, loc, cha, st: UTCDateTime, et: UTCDateTime):
        st_ts = st.timestamp
        et_ts = et.timestamp

        result = Stream([])
        assert self.rank == 0, 'Waveforms must be accessed from Rank 0. Aborting..'
        try:
            target_index = self.tree[net][sta][loc][cha]

            if(type(target_index) == index.Index):
                file_indices = np.array(list(target_index.intersection((st_ts, 1, et_ts, 1))), dtype='i4')

                for mfile in self.mseed_files[file_indices]:
                    result += read(mfile).slice(st, et, nearest_sample=False).copy()
                # end for
            # end if
        except Exception as e:
            print(str(e))
        # end try

        return result
    # end func
# end func

if __name__=="__main__":
    msi = MseedIndex('/g/data/ha3/ac5759/semi-perm-iris', '*.mseed')

    if(msi.rank == 0):
        print(msi.tree['AU'].keys())
        print(msi.tree['AU']['AXCOZ'].keys())
        r = msi.get_waveforms('AU', 'AXCOZ', '00', 'HHN', UTCDateTime("2020-10-01"), UTCDateTime("2020-10-01-T00:01:00"))
        print(r)
    # end if
# end if
