import json
import pytest
from PIL import Image
from inspector import inspect, InspectionError
from synthetic import generate

@pytest.mark.parametrize('count', [1, 8])
def test_frames_and_metadata(tmp_path, count):
    source = tmp_path/'sample.dcm'
    ds = generate(source, count)
    result = inspect(source, tmp_path/'out')
    assert result['decoded_frames'] == count
    assert len(list((tmp_path/'out').glob('frame-*.png'))) == count
    assert Image.open(tmp_path/'out/cine.gif').n_frames == count
    assert result['normalized']['StudyInstanceUID']['value'] == ds.StudyInstanceUID
    assert result['normalized']['Rows']['value'] == '64'
    assert json.loads((tmp_path/'out/metadata.json').read_text()) == result


def test_missing_and_provenance(tmp_path):
    source = tmp_path/'sample.dcm'
    generate(source, 1, optional=False)
    result = inspect(source, tmp_path/'out')
    assert result['normalized']['Manufacturer']['status'] == 'absent'
    assert result['normalized']['NumberOfFrames']['status'] == 'absent'
    assert result['normalized']['frame_rate']['value'] is None
    assert result['measurements']['status'] == 'unresolved'


def test_normalization(tmp_path):
    source = tmp_path/'sample.dcm'
    generate(source)
    result = inspect(source, tmp_path/'out')
    fps = result['normalized']['frame_rate']
    assert fps['value'] == 10
    assert fps['raw']['tag'] == '(0018,1063)'
    assert fps['raw']['value'] == '100'
    assert fps['source_unit'] == 'ms' and fps['result_unit'] == 'frames/s'


def test_corrupt_pixels(tmp_path):
    source = tmp_path/'sample.dcm'
    ds = generate(source)
    ds.PixelData = b'\0\0'
    ds.save_as(source, enforce_file_format=True)
    with pytest.raises(InspectionError, match='Pixel decode failed'):
        inspect(source, tmp_path/'out')


def test_unsupported(tmp_path):
    source = tmp_path/'sample.dcm'
    ds = generate(source)
    ds.file_meta.TransferSyntaxUID = '1.2.840.10008.1.2.4.50'
    from pydicom.encaps import encapsulate
    ds.PixelData = encapsulate([b'fake'])
    ds.save_as(source, enforce_file_format=True)
    with pytest.raises(InspectionError, match='unsupported.*1.2.840'):
        inspect(source, tmp_path/'out')


def test_invalid_file(tmp_path):
    source = tmp_path/'bad.dcm'
    source.write_text('not DICOM')
    with pytest.raises(InspectionError, match='Cannot parse'):
        inspect(source, tmp_path/'out')

@pytest.mark.parametrize('syntax', ['1.2.840.10008.1.2', '1.2.840.10008.1.2.2'])
def test_other_native_syntaxes(tmp_path, syntax):
    source = tmp_path/'sample.dcm'
    ds = generate(source)
    ds.file_meta.TransferSyntaxUID = syntax
    import pydicom
    pydicom.dcmwrite(source, ds, enforce_file_format=True, little_endian=syntax != '1.2.840.10008.1.2.2', implicit_vr=syntax == '1.2.840.10008.1.2')
    assert inspect(source, tmp_path/'out')['decoded_frames'] == 8
