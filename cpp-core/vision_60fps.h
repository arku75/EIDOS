#pragma once

#include <opencv2/opencv.hpp>
#include <thread>
#include <atomic>
#include <queue>
#include <mutex>

class Vision60FPS {
private:
    cv::VideoCapture capture;
    std::atomic<bool> running;
    std::queue<cv::Mat> frame_buffer;
    std::mutex buffer_mutex;
    std::thread capture_thread;

    void capture_loop();

public:
    Vision60FPS();
    ~Vision60FPS();
    bool start(int camera_id = 0);
    void stop();
    cv::Mat get_frame();
    bool has_frame();
};
