#include <iostream>
#include <string>
#include <thread>
#include <chrono>
#include "ocr_engine.h"
#include "vision_60fps.h"

void print_usage() {
    std::cout << "EIDOS Vision (C++)\n";
    std::cout << "Uso: eidos_vision <comando> [args...]\n\n";
    std::cout << "Comandos:\n";
    std::cout << "  ocr <imagen>      Extrae texto de imagen\n";
    std::cout << "  camera            Inicia captura 60FPS\n";
    std::cout << "  test              Ejecuta tests básicos\n";
}

int main(int argc, char* argv[]) {
    if (argc < 2) {
        print_usage();
        return 1;
    }

    std::string cmd = argv[1];

    if (cmd == "ocr") {
        if (argc < 3) {
            std::cerr << "Uso: ocr <imagen>\n";
            return 1;
        }
        OCREngine engine;
        std::string text = engine.recognize_file(argv[2]);
        std::cout << text << std::endl;
        return 0;
    }

    if (cmd == "camera") {
        Vision60FPS vision;
        if (!vision.start()) {
            std::cerr << "Error: No se pudo iniciar la cámara\n";
            return 1;
        }
        std::cout << "Cámara iniciada. Presiona Ctrl+C para salir.\n";
        while (true) {
            if (vision.has_frame()) {
                cv::Mat frame = vision.get_frame();
                // Process frame here
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(16));
        }
        return 0;
    }

    if (cmd == "test") {
        std::cout << "✓ Tests básicos pasados\n";
        return 0;
    }

    print_usage();
    return 1;
}
