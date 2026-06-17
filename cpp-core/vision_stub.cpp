#include <iostream>
#include <string>

extern "C" {
    // Stub OCR function
    const char* ocr_recognize(const char* image_path) {
        return "OCR not available - OpenCV/Tesseract not installed";
    }
    
    // Stub vision function
    int vision_start_camera(int camera_id) {
        std::cerr << "Vision camera not available - OpenCV not installed" << std::endl;
        return -1;
    }
    
    // Stub stop function
    void vision_stop_camera() {
        // No-op
    }
}
