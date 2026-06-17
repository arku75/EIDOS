#pragma once

#include <opencv2/opencv.hpp>
#include <string>
#include <map>

namespace tesseract { class TessBaseAPI; }

class OCREngine {
private:
    tesseract::TessBaseAPI* api;
    std::map<std::string, std::string> cache;

public:
    OCREngine();
    ~OCREngine();
    std::string recognize(const cv::Mat& image);
    std::string recognize_file(const std::string& path);
};
