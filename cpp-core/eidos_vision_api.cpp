#include "eidos_vision_api.h"
#include "vision_60fps.h"
#include "ocr_engine.h"
#include <cstring>
#include <cstdlib>

extern "C" {

// ── Vision 60FPS ──────────────────────────────────────────────────────────

VisionHandle vision_create(void) {
    return new Vision60FPS();
}

int vision_start(VisionHandle handle, int camera_id) {
    auto* v = static_cast<Vision60FPS*>(handle);
    return v->start(camera_id) ? 0 : -1;
}

int vision_has_frame(VisionHandle handle) {
    auto* v = static_cast<Vision60FPS*>(handle);
    return v->has_frame() ? 1 : 0;
}

int vision_get_frame(VisionHandle handle, unsigned char* buffer,
                     int* out_width, int* out_height) {
    auto* v = static_cast<Vision60FPS*>(handle);
    cv::Mat frame = v->get_frame();
    if (frame.empty()) return -1;

    *out_width = frame.cols;
    *out_height = frame.rows;
    int size = frame.cols * frame.rows * 3;
    std::memcpy(buffer, frame.data, size);
    return size;
}

void vision_stop(VisionHandle handle) {
    auto* v = static_cast<Vision60FPS*>(handle);
    v->stop();
}

void vision_destroy(VisionHandle handle) {
    auto* v = static_cast<Vision60FPS*>(handle);
    delete v;
}

// ── OCR ───────────────────────────────────────────────────────────────────

OCRHandle ocr_create(void) {
    return new OCREngine();
}

char* ocr_recognize_file(OCRHandle handle, const char* image_path) {
    auto* engine = static_cast<OCREngine*>(handle);
    std::string result = engine->recognize_file(std::string(image_path));
    char* out = static_cast<char*>(std::malloc(result.size() + 1));
    std::strcpy(out, result.c_str());
    return out;
}

char* ocr_recognize_buffer(OCRHandle handle, const unsigned char* buffer,
                           int width, int height) {
    auto* engine = static_cast<OCREngine*>(handle);
    cv::Mat image(height, width, CV_8UC3, const_cast<unsigned char*>(buffer));
    std::string result = engine->recognize(image);
    char* out = static_cast<char*>(std::malloc(result.size() + 1));
    std::strcpy(out, result.c_str());
    return out;
}

void ocr_free_string(char* str) {
    std::free(str);
}

void ocr_destroy(OCRHandle handle) {
    auto* engine = static_cast<OCREngine*>(handle);
    delete engine;
}

} // extern "C"
