"""Generate original synthetic moving-pattern ultrasound-format data, not anatomy."""
from pathlib import Path
import numpy as np
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, UltrasoundMultiFrameImageStorage, UltrasoundImageStorage, generate_uid


def generate(path, frames=8, optional=True):
    meta = FileMetaDataset()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID = UltrasoundMultiFrameImageStorage if frames > 1 else UltrasoundImageStorage
    meta.MediaStorageSOPInstanceUID = generate_uid()
    ds = FileDataset(str(path), {}, file_meta=meta, preamble=b'\0'*128)
    ds.SOPClassUID, ds.SOPInstanceUID = meta.MediaStorageSOPClassUID, meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID, ds.SeriesInstanceUID = generate_uid(), generate_uid()
    ds.Modality = 'US'
    ds.Rows = ds.Columns = 64
    ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, 'MONOCHROME2'
    ds.BitsAllocated = ds.BitsStored = 8
    ds.HighBit, ds.PixelRepresentation = 7, 0
    if frames > 1:
        ds.NumberOfFrames = frames
    if optional:
        ds.FrameTime, ds.Manufacturer, ds.ManufacturerModelName = '100', 'SYNTHETIC', 'Generated pattern'
    arr = np.zeros((frames, 64, 64), dtype=np.uint8)
    for i in range(frames):
        arr[i, 12:40, (i*4)%40:(i*4)%40+16] = 100+i
    ds.PixelData = arr.tobytes()
    ds.save_as(path, enforce_file_format=True)
    return ds

if __name__ == '__main__':
    import sys
    generate(Path(sys.argv[1]))
