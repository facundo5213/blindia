# Why a patched rapidocr wheel lives here

`blindia.ocr` uses [RapidOCR](https://github.com/RapidAI/RapidOCR) (ONNXRuntime
backend, PP-OCRv5 mobile weights) for text extraction. It was chosen over the
alternatives after benchmarking directly on this board's actual app container:

- **Tesseract**: fastest and lightest in isolation, but requires an external
  `tesseract` system binary. This app framework only supports installing
  PyPI packages (`python/requirements.txt`), with no supported mechanism to
  add OS packages to the container -- so it can't be deployed here.
- **Native PaddleOCR** (`paddlepaddle`): its generic aarch64 wheel crashes
  with SIGSEGV on this board's CPU, which is missing the `asimddp`/`fphp`
  ARMv8.2 SIMD extensions the wheel's kernels assume.
- **RapidOCR**: pure pip-installable, ONNXRuntime backend avoids the SIMD
  crash. Confirmed working in-container. ~2-3s/image, ~500MB RAM peak.

## The wheel patch

RapidOCR's PyPI metadata hard-requires `opencv_python` (the GUI build, which
needs `libGL.so.1`). This app's base image only has `opencv-python-headless`
preinstalled (no `libGL.so.1`, and no supported way to add it). A plain
`requirements.txt` entry for `rapidocr` makes `uv pip install` fetch the GUI
`opencv_python` as a transitive dependency, which then shadows the working
headless `cv2` already on the system path -- breaking every import with
`ImportError: libGL.so.1: cannot open shared object file`.

`rapidocr-3.9.2-py3-none-any.whl` here is the unmodified upstream wheel with
one line removed from its `METADATA` (`Requires-Dist: opencv_python>=...`).
It's a pure-Python package -- nothing in its actual code changed, only the
dependency declaration pip/uv reads. `run.sh` installs local wheels from this
`python-libraries/` folder (see the base image's `/run.sh`), so it picks this
up instead of fetching the real `rapidocr` from PyPI. Its other real
dependencies (`onnxruntime`, `pyclipper`, `Shapely`, `PyYAML`, `tqdm`,
`omegaconf`, `colorlog`, `numpy`, `Pillow`, `requests`) are untouched and
still resolve normally.

**If rapidocr is ever upgraded**, redo the patch: download the new wheel
(`pip download --no-deps rapidocr`), remove the `Requires-Dist: opencv_python`
line from `*.dist-info/METADATA`, repack it (`python -m wheel pack <dir>`),
and replace this file.
