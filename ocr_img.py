import easyocr

reader = easyocr.Reader(["ch_sim", "en"], gpu=False, verbose=False)
result = reader.readtext(
    r"C:\Users\liu\.lumen\session-files\8595876131\photo_AQADOQ1rGyjAcUV9_1781433049_39e65504.jpg", detail=1
)
for item in result:
    print(f"{item[1]}")
