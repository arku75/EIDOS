#pragma once

#ifdef __cplusplus
extern "C" {
#endif

// Opaque handles for Python ctypes
typedef void* VisionHandle;
typedef void* OCRHandle;

// Vision 60FPS API
VisionHandle vision_create(void);
int          vision_start(VisionHandle handle, int camera_id);
int          vision_has_frame(VisionHandle handle);
// Writes frame data to buffer. Returns bytes written, or -1 on error.
// buffer must be at least width*height*3 bytes (BGR).
int          vision_get_frame(VisionHandle handle, unsigned char* buffer,
                              int* out_width, int* out_height);
void         vision_stop(VisionHandle handle);
void         vision_destroy(VisionHandle handle);

// OCR API
OCRHandle    ocr_create(void);
// Recognize text from image file. Returns allocated string (caller must free with ocr_free_string).
char*        ocr_recognize_file(OCRHandle handle, const char* image_path);
// Recognize text from raw BGR buffer.
char*        ocr_recognize_buffer(OCRHandle handle, const unsigned char* buffer,
                                  int width, int height);
void         ocr_free_string(char* str);
void         ocr_destroy(OCRHandle handle);

#ifdef __cplusplus
}
#endif
