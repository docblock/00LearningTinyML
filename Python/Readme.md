Link zur Browser IDE von Espressif:
https://ide.vision.espressif.com/

Link zur Übersicht von Espressif zu ESP Vision:
https://vision.espressif.com/en

# Damit man mit Python auf dem ESP32 arbeiten kann, benötigt man eine passende Firmware\
Variante 1: Firmware selbst erzeugen\
Infos zur Erstellung der Firmware:
git clone --recursive https://github.com/espressif/esp-vision.git esp-vision \
cd esp-vision \
git apply waveshare-p4-v1.3-ov5647.patch \

Dann sdkconfig in den Build-Ordner verschieben \

Variante 2: fertige Firmware verwenden\
Alternativ: fertige Firmware auf den ESP32 hochladen: \
esptool.py --chip esp32p4 --port <PORT> --baud 460800 write_flash 0x0 esp-vision-waveshare-p4-v1.3-ov5647.bin


