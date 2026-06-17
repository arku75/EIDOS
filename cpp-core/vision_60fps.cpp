#include "vision_60fps.h"

Vision60FPS::Vision60FPS() : running(false) {}

Vision60FPS::~Vision60FPS() {
    stop();
}

bool Vision60FPS::start(int camera_id) {
    capture.open(camera_id, cv::CAP_V4L2);
    if (!capture.isOpened()) {
        capture.open(camera_id);
    }

    if (!capture.isOpened()) {
        return false;
    }

    // Set 60fps capture
    capture.set(cv::CAP_PROP_FPS, 60);
    capture.set(cv::CAP_PROP_FRAME_WIDTH, 1280);
    capture.set(cv::CAP_PROP_FRAME_HEIGHT, 720);

    running = true;
    capture_thread = std::thread(&Vision60FPS::capture_loop, this);
    return true;
}

void Vision60FPS::stop() {
    running = false;
    if (capture_thread.joinable()) {
        capture_thread.join();
    }
    if (capture.isOpened()) {
        capture.release();
    }
}

cv::Mat Vision60FPS::get_frame() {
    std::lock_guard<std::mutex> lock(buffer_mutex);
    if (frame_buffer.empty()) {
        return cv::Mat();
    }
    cv::Mat frame = frame_buffer.front();
    frame_buffer.pop();
    return frame;
}

bool Vision60FPS::has_frame() {
    std::lock_guard<std::mutex> lock(buffer_mutex);
    return !frame_buffer.empty();
}

void Vision60FPS::capture_loop() {
    while (running) {
        cv::Mat frame;
        if (capture.read(frame)) {
            std::lock_guard<std::mutex> lock(buffer_mutex);
            if (frame_buffer.size() < 3) {
                frame_buffer.push(frame.clone());
            }
        }
    }
}
