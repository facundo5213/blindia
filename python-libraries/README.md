# Por qué vive acá un wheel parcheado de rapidocr

`blindia.ocr` usa [RapidOCR](https://github.com/RapidAI/RapidOCR) (backend
ONNXRuntime, pesos PP-OCRv5 mobile) para extracción de texto. Se eligió por
sobre las alternativas después de benchmarkear directamente en el contenedor
real de la app en esta placa:

- **Tesseract**: el más rápido y liviano en aislamiento, pero requiere un
  binario `tesseract` externo del sistema. Este framework de apps solo
  soporta instalar paquetes de PyPI (`python/requirements.txt`), sin ningún
  mecanismo soportado para agregar paquetes del sistema operativo al
  contenedor -- así que no se puede desplegar acá.
- **PaddleOCR nativo** (`paddlepaddle`): su wheel genérico aarch64 crashea
  con SIGSEGV en la CPU de esta placa, a la que le faltan las extensiones
  SIMD `asimddp`/`fphp` de ARMv8.2 que asumen los kernels del wheel.
- **RapidOCR**: instalable puramente por pip, el backend ONNXRuntime evita
  el crash de SIMD. Confirmado funcionando dentro del contenedor. ~2-3s por
  imagen, ~500MB de pico de RAM.

## El parche del wheel

Los metadatos de PyPI de RapidOCR exigen a la fuerza `opencv_python` (la
build con GUI, que necesita `libGL.so.1`). La imagen base de esta app solo
tiene preinstalado `opencv-python-headless` (sin `libGL.so.1`, y sin forma
soportada de agregarlo). Una entrada común de `requirements.txt` para
`rapidocr` hace que `uv pip install` traiga `opencv_python` (la GUI) como
dependencia transitiva, que después tapa el `cv2` headless que ya funciona
en el path del sistema -- rompiendo todos los imports con
`ImportError: libGL.so.1: cannot open shared object file`.

`rapidocr-3.9.2-py3-none-any.whl` acá es el wheel original de upstream sin
modificar, con una sola línea sacada de su `METADATA`
(`Requires-Dist: opencv_python>=...`). Es un paquete puro Python -- nada de
su código real cambió, solo la declaración de dependencias que lee pip/uv.
`run.sh` instala wheels locales desde esta carpeta `python-libraries/` (ver
el `/run.sh` de la imagen base), así que toma este en vez de bajar el
`rapidocr` real de PyPI. Sus otras dependencias reales (`onnxruntime`,
`pyclipper`, `Shapely`, `PyYAML`, `tqdm`, `omegaconf`, `colorlog`, `numpy`,
`Pillow`, `requests`) quedan intactas y siguen resolviendo normalmente.

**Si alguna vez se actualiza rapidocr**, rehacer el parche: bajar el wheel
nuevo (`pip download --no-deps rapidocr`), sacar la línea
`Requires-Dist: opencv_python` de `*.dist-info/METADATA`, reempaquetarlo
(`python -m wheel pack <dir>`), y reemplazar este archivo.
