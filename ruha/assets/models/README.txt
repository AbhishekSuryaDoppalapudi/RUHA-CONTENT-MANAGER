Face detection models used for speaker-aware reframing (ruha/services/reframe.py). Bundled so nothing is downloaded.

blaze_face_short_range.tflite
    MediaPipe BlazeFace short-range face detector. Apache License 2.0, Google LLC.
    Source: https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite
    Made for close-ups: it misses small faces in a wide shot, which is why OpenCV's detector below is merged with it.

face_detection_yunet_2023mar.onnx
    YuNet face detector (OpenCV Zoo, by Shiqi Yu). MIT License.
    Source: https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet
