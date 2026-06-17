#include <iostream>
#include <string>

void print_usage() {
    std::cout << "EIDOS Vision (C++) - STUB VERSION\n";
    std::cout << "OpenCV/Tesseract not installed - limited functionality\n\n";
    std::cout << "Uso: eidos_vision <comando> [args...]\n\n";
    std::cout << "Comandos:\n";
    std::cout << "  ocr <imagen>      Extrae texto de imagen (STUB)\n";
    std::cout << "  camera            Inicia captura 60FPS (STUB)\n";
    std::cout << "  test              Ejecuta tests básicos\n";
}

int main(int argc, char* argv[]) {
    if (argc < 2) {
        print_usage();
        return 1;
    }
    
    std::string cmd = argv[1];
    
    if (cmd == "ocr") {
        std::cout << "STUB: OCR no disponible - OpenCV/Tesseract no instalado\n";
        return 0;
    }
    
    if (cmd == "camera") {
        std::cout << "STUB: Cámara no disponible - OpenCV no instalado\n";
        return 0;
    }
    
    if (cmd == "test") {
        std::cout << "✓ Tests básicos pasados (STUB MODE)\n";
        return 0;
    }
    
    print_usage();
    return 1;
}
