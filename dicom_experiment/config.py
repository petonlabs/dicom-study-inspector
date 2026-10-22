"""Shared experiment policy and field definitions; no I/O or service dependencies."""

GROUPS = {
    "hierarchy": "StudyInstanceUID SeriesInstanceUID SOPInstanceUID FrameOfReferenceUID",
    "acquisition": "Modality ImageType Manufacturer ManufacturerModelName ProtocolName SeriesDescription",
    "spatial": "PixelSpacing ImagePositionPatient ImageOrientationPatient SliceThickness SpacingBetweenSlices",
    "temporal": "StudyDate StudyTime AcquisitionDate AcquisitionTime AcquisitionDateTime TimezoneOffsetFromUTC FrameTime FrameTimeVector TemporalPositionIdentifier TemporalPositionIndex",
    "pixel": "Rows Columns NumberOfFrames SamplesPerPixel PhotometricInterpretation BitsAllocated BitsStored HighBit PixelRepresentation RescaleSlope RescaleIntercept WindowCenter WindowWidth",
    "CT": "RescaleType KVP Exposure ExposureTime XRayTubeCurrent",
    "MR": "RepetitionTime EchoTime InversionTime FlipAngle MagneticFieldStrength ScanningSequence SequenceVariant SequenceName",
    "PT": "Units DecayCorrection CorrectedImage RadiopharmaceuticalInformationSequence Radiopharmaceutical RadionuclideTotalDose RadionuclideHalfLife RadiopharmaceuticalStartTime RadiopharmaceuticalStartDateTime",
    "DX": "ViewPosition ImageLaterality Laterality ImagerPixelSpacing Exposure ExposureTime KVP",
}

COMMON = {
    k
    for group, fields in GROUPS.items()
    if group not in ("CT", "MR", "PT", "DX")
    for k in fields.split()
}

PIXEL_TAGS = {"7FE00010", "7FE00008", "7FE00009"}

LOSSLESS_SYNTAXES = {
    "1.2.840.10008.1.2.4." + suffix for suffix in ("57", "70", "80", "90")
}

MAX_INSTANCES = 20
MAX_DOWNLOAD_BYTES = 100 * 1024**2
MAX_DECODED_SAMPLES = 64_000_000
METADATA_RESPONSE_LIMIT = 4 * 1024**2
STUDY_LIST_LIMIT = 200
SERIES_LIST_LIMIT = 100
INSTANCE_LIST_LIMIT = 10
GDCM_VERSION = "3.2.6"
S3_ROOT = "https://sid-sijbrandij-osteosarc-dataset.s3.us-west-2.amazonaws.com/"
CATALOG_URL = "https://osteosarc.com/imaging/dicom/"
LICENSE_URL = "https://registry.opendata.aws/sid-osteosarc/"
DATASET_NAME = "Osteosarc public DICOM archive"
USER_AGENT = "dicom-study-inspector/1.0 (local research)"
CONDITIONS = ("dicom", "png_only", "png_sidecar")
RECOVERY_FIELDS = (
    "StudyInstanceUID",
    "SeriesInstanceUID",
    "SOPInstanceUID",
    "ImagePositionPatient",
    "ImageOrientationPatient",
    "PixelSpacing",
    "StudyDate",
    "StudyTime",
)
COUNTER_FIELDS = ("all_eligible", "all_matched", "focused_eligible", "focused_matched")

# Artifact names are part of the stable CLI/on-disk contract.
METADATA_FILE = "metadata.json"
CHECKSUMS_FILE = "checksums.json"
EXPORT_MANIFEST_FILE = "export_manifest.json"
SUMMARY_FILE = "summary.json"
