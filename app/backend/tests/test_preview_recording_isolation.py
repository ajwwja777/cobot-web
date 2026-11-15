import io
import numpy as np
from PIL import Image
from cobot_console.camera_sync import _jpeg


def test_preview_is_bounded_without_changing_native_frame():
    image=np.full((1224,1632,3),100,dtype=np.uint8)
    preview=Image.open(io.BytesIO(_jpeg(image)))
    assert preview.width<=960 and preview.height<=720
    assert image.shape==(1224,1632,3) and np.all(image==100)
    assert Image.open(io.BytesIO(_jpeg(np.zeros((480,640,3),dtype=np.uint8)))).size==(640,480)
