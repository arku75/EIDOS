#include "ocr_engine.h"
#include <tesseract/baseapi.h>
#include <leptonica/allheaders.h>

OCREngine::OCREngine() {
    api = new tesseract::TessBaseAPI();
    if (api->Init(NULL, "eng+spa")) {
        fprintf(stderr, "Could not initialize tesseract.\n");
    }
}

OCREngine::~OCREngine() {
    api->End();
    delete api;
}

std::string OCREngine::recognize(const cv::Mat& image) {
    if (image.empty()) return "";

    // Convert to grayscale
    cv::Mat gray;
    cv::cvtColor(image, gray, cv::COLOR_BGR2GRAY);

    // Denoise
    cv::Mat denoised;
    cv::fastNlMeansDenoising(gray, denoised);

    // Binarize
    cv::Mat binary;
    cv::adaptiveThreshold(denoised, binary, 255,
        cv::ADAPTIVE_THRESH_GAUSSIAN_C, cv::THRESH_BINARY, 11, 2);

    // OCR
    api->SetImage(binary.data, binary.cols, binary.rows, 1, binary.step);
    char* text = api->GetUTF8Text();
    std::string result(text);
    delete[] text;

    return result;
}

std::string OCREngine::recognize_file(const std::string& path) {
    cv::Mat image = cv::imread(path);
    return recognize(image);
}
