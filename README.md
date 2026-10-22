# DICOM Study Inspector

Bounded local prototype: parse one DICOM, decode native pixels, emit an animated GIF, every PNG frame, a JSON inventory and an HTML inspection page. No inference, network calls, or diagnostic interpretation.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pytest -q
mkdir -p demo
.venv/bin/python synthetic.py demo/synthetic.dcm
.venv/bin/python inspector.py demo/synthetic.dcm --output demo/output
open demo/output/index.html
```

The generated fixture is original synthetic moving rectangles (64×64, eight frames); it resembles no patient acquisition and is not a full ultrasound IOD conformance fixture. Never treat it as diagnostic validation. UID identity is newly generated per fixture; the inventory hashes the exact source bytes.

Native Explicit VR Little Endian, Implicit VR Little Endian and Explicit VR Big Endian are supported and tested on synthetic 8-bit monochrome data. Compressed and proprietary transfer syntaxes are rejected clearly; no proprietary tooling needed. MONOCHROME1/2 and RGB display paths exist; clinical display calibration, color validation and full IOD validation are outside this slice. See [pydicom decoding documentation](https://pydicom.github.io/pydicom/stable/tutorials/pixel_data/introduction.html).

Raw inventory excludes pixel bytes and retains standard/private sequences, but binary entries are omitted. Normalized values include source tag, raw decoded value, transform, units and status. Values are parsed DICOM representations, not original byte encodings; the source SHA identifies the retained original. Measurements remain unresolved because no validated semantic mapper exists; no 2D/biplane/3D quantities are conflated. Missing optional fields remain absent. FrameTime yields frame rate; FrameTimeVector is inventoried but variable playback timing is not implemented. GIF display uses global 8-bit scaling, rounded timing and a clearly documented 100ms fallback if timing is absent. Individual PNG frames are the count-preserving artifact when GIF optimization coalesces identical frames.

Use synthetic inputs for this demo. Inventory can retain identifiers and private tags: this is not a de-identification tool. File size and decoded sample limits bound this prototype, but it is not hardened for arbitrary hostile files. Outputs are local and should remain local for clinical data.

## Bounded real-data comparison

See [the local experiment protocol and baseline audit](REAL_DATA_EXPERIMENT.md)
for the separate `study_compare.py acquire`, `compare`, and `verify` commands.
Only acquisition accesses the network; source DICOM and all patient-level outputs
remain in ignored `review/`. PNG metadata retention and pixel quantization are
measured without making claims about clinical information loss.
