import pyaudio
import wave

def test_raw_mic():
    CHUNK = 1024
    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    RATE = 44100
    RECORD_SECONDS = 5
    WAVE_OUTPUT_FILENAME = "test_mic.wav"

    p = pyaudio.PyAudio()

    print("🎤 [RAW TEST] Grabando 5 segundos... (HOLA HOLA)")
    
    try:
        stream = p.open(format=FORMAT,
                        channels=CHANNELS,
                        rate=RATE,
                        input=True,
                        frames_per_buffer=CHUNK)

        frames = []

        for i in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
            data = stream.read(CHUNK)
            frames.append(data)

        print("🛑 Grabación terminada.")

        stream.stop_stream()
        stream.close()
        p.terminate()

        wf = wave.open(WAVE_OUTPUT_FILENAME, 'wb')
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(p.get_sample_size(FORMAT))
        wf.setframerate(RATE)
        wf.writeframes(b''.join(frames))
        wf.close()
        
        print(f"✅ Archivo guardado: {WAVE_OUTPUT_FILENAME}")
        print("   Intenta reproducirlo con: aplay test_mic.wav")
        return True
        
    except Exception as e:
        print(f"❌ Error Grabando: {e}")
        return False

if __name__ == "__main__":
    test_raw_mic()
