"""Local, bounded DICOM inspector. No network access or clinical interpretation."""
import argparse
import hashlib
import html
import json
from pathlib import Path

import numpy as np
import pydicom
from PIL import Image
from pydicom.uid import ExplicitVRLittleEndian, ImplicitVRLittleEndian, ExplicitVRBigEndian

SUPPORTED = {str(ExplicitVRLittleEndian), str(ImplicitVRLittleEndian), str(ExplicitVRBigEndian)}
FIELDS = ['StudyInstanceUID', 'SeriesInstanceUID', 'SOPInstanceUID', 'Modality',
          'Rows', 'Columns', 'NumberOfFrames', 'PhotometricInterpretation',
          'Manufacturer', 'ManufacturerModelName', 'FrameTime', 'FrameTimeVector',
          'CineRate', 'HeartRate', 'AcquisitionDateTime']

class InspectionError(ValueError):
    pass


def field(ds, name, unit=None):
    element = ds.data_element(name) if name in ds else None
    return {'value': str(element.value) if element is not None else None,
            'raw': None if element is None else {'tag': str(element.tag), 'vr': element.VR, 'value': str(element.value)},
            'transformation': 'identity', 'source_unit': unit, 'result_unit': unit,
            'status': 'present' if element is not None else 'absent'}


def inspect(source, output):
    source, output = Path(source), Path(output)
    if source.stat().st_size > 128 * 1024**2:
        raise InspectionError('Input exceeds prototype 128 MiB limit')
    try:
        ds = pydicom.dcmread(source, force=False)
    except Exception as exc:
        raise InspectionError(f'Cannot parse DICOM: {exc}') from exc
    syntax = str(getattr(ds.file_meta, 'TransferSyntaxUID', ''))
    if syntax not in SUPPORTED:
        raise InspectionError(f'Pixel decode unsupported for transfer syntax {syntax}; prototype supports native uncompressed syntaxes only')
    try:
        count = int(getattr(ds, 'NumberOfFrames', 1))
        rows, cols = int(ds.Rows), int(ds.Columns)
        samples = int(ds.SamplesPerPixel)
        if min(count, rows, cols) < 1 or count * rows * cols * samples > 64_000_000:
            raise ValueError('invalid dimensions or decoded sample limit exceeded')
        if ds.PhotometricInterpretation not in ('MONOCHROME1', 'MONOCHROME2', 'RGB'):
            raise ValueError('only MONOCHROME1/2 and RGB display supported')
        pixels = ds.pixel_array
        frames = pixels[np.newaxis, ...] if count == 1 else pixels
        if len(frames) != count or frames.shape[1:3] != (rows, cols):
            raise ValueError('decoded frames disagree with metadata')
    except Exception as exc:
        raise InspectionError(f'Pixel decode failed ({syntax}): {exc}') from exc
    normalized = {name: field(ds, name, 'ms' if name in ('FrameTime', 'FrameTimeVector') else None) for name in FIELDS}
    normalized['TransferSyntaxUID'] = field(ds.file_meta, 'TransferSyntaxUID')
    timing = normalized['FrameTime']
    fps = {'value': None, 'raw': timing['raw'], 'transformation': '1000 / FrameTime', 'source_unit': 'ms', 'result_unit': 'frames/s', 'status': 'unresolved'}
    if timing['value'] is not None and float(timing['value']) > 0:
        fps.update(value=1000 / float(timing['value']), status='derived')
    normalized['frame_rate'] = fps
    inventory = ds.to_json_dict(bulk_data_threshold=0, bulk_data_element_handler=lambda element: 'omitted:binary')
    inventory.pop('7FE00010', None)
    result = {'schema_version': 1, 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
              'decoded_frames': count, 'normalized': normalized, 'raw_inventory': inventory,
              'measurements': {'status': 'unresolved', 'values': [], 'reason': 'No semantic measurement mapper implemented; sequences retained in raw inventory, methods never equated'},
              'display': {'transform': 'global min/max to 8-bit; invert MONOCHROME1; preview only', 'timing': 'FrameTime when positive; otherwise 100ms preview fallback; variable timing not rendered'}}
    output.mkdir(parents=True, exist_ok=True)
    low, high = float(frames.min()), float(frames.max())
    shown = np.zeros_like(frames, dtype=np.uint8) if high == low else ((frames.astype(float)-low)*255/(high-low)).astype(np.uint8)
    if ds.PhotometricInterpretation == 'MONOCHROME1':
        shown = 255-shown
    images = [Image.fromarray(frame) for frame in shown]
    duration = float(timing['value']) if fps['value'] is not None else 100
    images[0].save(output/'cine.gif', save_all=True, append_images=images[1:], duration=max(10, round(duration)), loop=0, optimize=False)
    for i, image in enumerate(images):
        image.save(output/f'frame-{i:04d}.png')
    (output/'metadata.json').write_text(json.dumps(result, indent=2, sort_keys=True)+'\n')
    (output/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>DICOM Study Inspector</title><h1>DICOM Study Inspector</h1><p>Local preview. No diagnostic interpretation. Measurements unresolved.</p><img src="cine.gif" alt="Decoded cine"><h2>Metadata and provenance</h2><pre>'+html.escape(json.dumps(result, indent=2))+'</pre>')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        result = inspect(args.source, args.output)
    except (InspectionError, OSError) as exc:
        parser.exit(1, f'{exc}\n')
    print(f"Decoded {result['decoded_frames']} frames; output: {args.output}")
