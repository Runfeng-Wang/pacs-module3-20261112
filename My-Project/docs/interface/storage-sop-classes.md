# 附录 A：B 作为 Storage SCP 声明支持的存储 SOP 类

由 `scripts/gen_storage_appendix.py` 根据 pynetdicom 3.0.4 的 `StoragePresentationContexts` 生成。
B 对所有这些类**原样保存**，不解析像素语义。证据级别：E0 = 仅声明未实测；E1 = 单元级；
E2 = pynetdicom 真实 TCP 回环；E3 = DCMTK 独立实现回环。

| # | SOP Class UID | 名称 | 证据级别 |
|---|---|---|---|
| 1 | 1.2.840.10008.5.1.1.27 | Stored Print Storage SOP Class | E0 |
| 2 | 1.2.840.10008.5.1.1.29 | Hardcopy Grayscale Image Storage SOP Class | E0 |
| 3 | 1.2.840.10008.5.1.1.30 | Hardcopy Color Image Storage SOP Class | E0 |
| 4 | 1.2.840.10008.5.1.4.1.1.1 | Computed Radiography Image Storage | E0 |
| 5 | 1.2.840.10008.5.1.4.1.1.1.1 | Digital X-Ray Image Storage - For Presentation | E0 |
| 6 | 1.2.840.10008.5.1.4.1.1.1.1.1 | Digital X-Ray Image Storage - For Processing | E0 |
| 7 | 1.2.840.10008.5.1.4.1.1.1.2 | Digital Mammography X-Ray Image Storage - For Presentation | E0 |
| 8 | 1.2.840.10008.5.1.4.1.1.1.2.1 | Digital Mammography X-Ray Image Storage - For Processing | E0 |
| 9 | 1.2.840.10008.5.1.4.1.1.1.3 | Digital Intra-Oral X-Ray Image Storage - For Presentation | E0 |
| 10 | 1.2.840.10008.5.1.4.1.1.1.3.1 | Digital Intra-Oral X-Ray Image Storage - For Processing | E0 |
| 11 | 1.2.840.10008.5.1.4.1.1.10 | Standalone Modality LUT Storage | E0 |
| 12 | 1.2.840.10008.5.1.4.1.1.104.1 | Encapsulated PDF Storage | E0 |
| 13 | 1.2.840.10008.5.1.4.1.1.104.2 | Encapsulated CDA Storage | E0 |
| 14 | 1.2.840.10008.5.1.4.1.1.11 | Standalone VOI LUT Storage | E0 |
| 15 | 1.2.840.10008.5.1.4.1.1.11.1 | Grayscale Softcopy Presentation State Storage | E0 |
| 16 | 1.2.840.10008.5.1.4.1.1.11.2 | Color Softcopy Presentation State Storage | E0 |
| 17 | 1.2.840.10008.5.1.4.1.1.11.3 | Pseudo-Color Softcopy Presentation State Storage | E0 |
| 18 | 1.2.840.10008.5.1.4.1.1.11.4 | Blending Softcopy Presentation State Storage | E0 |
| 19 | 1.2.840.10008.5.1.4.1.1.11.5 | XA/XRF Grayscale Softcopy Presentation State Storage | E0 |
| 20 | 1.2.840.10008.5.1.4.1.1.12.1 | X-Ray Angiographic Image Storage | E0 |
| 21 | 1.2.840.10008.5.1.4.1.1.12.1.1 | Enhanced XA Image Storage | E0 |
| 22 | 1.2.840.10008.5.1.4.1.1.12.2 | X-Ray Radiofluoroscopic Image Storage | E0 |
| 23 | 1.2.840.10008.5.1.4.1.1.12.2.1 | Enhanced XRF Image Storage | E0 |
| 24 | 1.2.840.10008.5.1.4.1.1.12.3 | X-Ray Angiographic Bi-Plane Image Storage | E0 |
| 25 | 1.2.840.10008.5.1.4.1.1.128 | Positron Emission Tomography Image Storage | E0 |
| 26 | 1.2.840.10008.5.1.4.1.1.128.1 | Legacy Converted Enhanced PET Image Storage | E0 |
| 27 | 1.2.840.10008.5.1.4.1.1.129 | Standalone PET Curve Storage | E0 |
| 28 | 1.2.840.10008.5.1.4.1.1.13.1.1 | X-Ray 3D Angiographic Image Storage | E0 |
| 29 | 1.2.840.10008.5.1.4.1.1.13.1.2 | X-Ray 3D Craniofacial Image Storage | E0 |
| 30 | 1.2.840.10008.5.1.4.1.1.13.1.3 | Breast Tomosynthesis Image Storage | E0 |
| 31 | 1.2.840.10008.5.1.4.1.1.130 | Enhanced PET Image Storage | E0 |
| 32 | 1.2.840.10008.5.1.4.1.1.131 | Basic Structured Display Storage | E0 |
| 33 | 1.2.840.10008.5.1.4.1.1.14.1 | Intravascular Optical Coherence Tomography Image Storage - For Presentation | E0 |
| 34 | 1.2.840.10008.5.1.4.1.1.14.2 | Intravascular Optical Coherence Tomography Image Storage - For Processing | E0 |
| 35 | 1.2.840.10008.5.1.4.1.1.2 | CT Image Storage | E2 + E3（pynetdicom 回环 + DCMTK 3.7.1） |
| 36 | 1.2.840.10008.5.1.4.1.1.2.1 | Enhanced CT Image Storage | E0 |
| 37 | 1.2.840.10008.5.1.4.1.1.2.2 | Legacy Converted Enhanced CT Image Storage | E0 |
| 38 | 1.2.840.10008.5.1.4.1.1.20 | Nuclear Medicine Image Storage | E0 |
| 39 | 1.2.840.10008.5.1.4.1.1.3 | Ultrasound Multi-frame Image Storage | E0 |
| 40 | 1.2.840.10008.5.1.4.1.1.3.1 | Ultrasound Multi-frame Image Storage | E0 |
| 41 | 1.2.840.10008.5.1.4.1.1.4 | MR Image Storage | E0 |
| 42 | 1.2.840.10008.5.1.4.1.1.4.1 | Enhanced MR Image Storage | E0 |
| 43 | 1.2.840.10008.5.1.4.1.1.4.2 | MR Spectroscopy Storage | E0 |
| 44 | 1.2.840.10008.5.1.4.1.1.4.3 | Enhanced MR Color Image Storage | E0 |
| 45 | 1.2.840.10008.5.1.4.1.1.4.4 | Legacy Converted Enhanced MR Image Storage | E0 |
| 46 | 1.2.840.10008.5.1.4.1.1.481.1 | RT Image Storage | E0 |
| 47 | 1.2.840.10008.5.1.4.1.1.481.2 | RT Dose Storage | E0 |
| 48 | 1.2.840.10008.5.1.4.1.1.481.3 | RT Structure Set Storage | E0 |
| 49 | 1.2.840.10008.5.1.4.1.1.481.4 | RT Beams Treatment Record Storage | E0 |
| 50 | 1.2.840.10008.5.1.4.1.1.481.5 | RT Plan Storage | E0 |
| 51 | 1.2.840.10008.5.1.4.1.1.481.6 | RT Brachy Treatment Record Storage | E0 |
| 52 | 1.2.840.10008.5.1.4.1.1.481.7 | RT Treatment Summary Record Storage | E0 |
| 53 | 1.2.840.10008.5.1.4.1.1.481.8 | RT Ion Plan Storage | E0 |
| 54 | 1.2.840.10008.5.1.4.1.1.481.9 | RT Ion Beams Treatment Record Storage | E0 |
| 55 | 1.2.840.10008.5.1.4.1.1.5 | Nuclear Medicine Image Storage | E0 |
| 56 | 1.2.840.10008.5.1.4.1.1.6 | Ultrasound Image Storage | E0 |
| 57 | 1.2.840.10008.5.1.4.1.1.6.1 | Ultrasound Image Storage | E0 |
| 58 | 1.2.840.10008.5.1.4.1.1.6.2 | Enhanced US Volume Storage | E0 |
| 59 | 1.2.840.10008.5.1.4.1.1.66 | Raw Data Storage | E0 |
| 60 | 1.2.840.10008.5.1.4.1.1.66.1 | Spatial Registration Storage | E0 |
| 61 | 1.2.840.10008.5.1.4.1.1.66.2 | Spatial Fiducials Storage | E0 |
| 62 | 1.2.840.10008.5.1.4.1.1.66.3 | Deformable Spatial Registration Storage | E0 |
| 63 | 1.2.840.10008.5.1.4.1.1.66.4 | Segmentation Storage | E0 |
| 64 | 1.2.840.10008.5.1.4.1.1.66.5 | Surface Segmentation Storage | E0 |
| 65 | 1.2.840.10008.5.1.4.1.1.67 | Real World Value Mapping Storage | E0 |
| 66 | 1.2.840.10008.5.1.4.1.1.68.1 | Surface Scan Mesh Storage | E0 |
| 67 | 1.2.840.10008.5.1.4.1.1.68.2 | Surface Scan Point Cloud Storage | E0 |
| 68 | 1.2.840.10008.5.1.4.1.1.7 | Secondary Capture Image Storage | E2（pynetdicom 回环，TC-R5-P2） |
| 69 | 1.2.840.10008.5.1.4.1.1.7.1 | Multi-frame Single Bit Secondary Capture Image Storage | E0 |
| 70 | 1.2.840.10008.5.1.4.1.1.7.2 | Multi-frame Grayscale Byte Secondary Capture Image Storage | E0 |
| 71 | 1.2.840.10008.5.1.4.1.1.7.3 | Multi-frame Grayscale Word Secondary Capture Image Storage | E0 |
| 72 | 1.2.840.10008.5.1.4.1.1.7.4 | Multi-frame True Color Secondary Capture Image Storage | E0 |
| 73 | 1.2.840.10008.5.1.4.1.1.77.1 | VL Image Storage - Trial | E0 |
| 74 | 1.2.840.10008.5.1.4.1.1.77.1.1 | VL Endoscopic Image Storage | E2（pynetdicom 回环，TC-R5-P2 / TC-R11-P1） |
| 75 | 1.2.840.10008.5.1.4.1.1.77.1.1.1 | Video Endoscopic Image Storage | E0 |
| 76 | 1.2.840.10008.5.1.4.1.1.77.1.2 | VL Microscopic Image Storage | E0 |
| 77 | 1.2.840.10008.5.1.4.1.1.77.1.2.1 | Video Microscopic Image Storage | E0 |
| 78 | 1.2.840.10008.5.1.4.1.1.77.1.3 | VL Slide-Coordinates Microscopic Image Storage | E0 |
| 79 | 1.2.840.10008.5.1.4.1.1.77.1.4 | VL Photographic Image Storage | E0 |
| 80 | 1.2.840.10008.5.1.4.1.1.77.1.4.1 | Video Photographic Image Storage | E0 |
| 81 | 1.2.840.10008.5.1.4.1.1.77.1.5.1 | Ophthalmic Photography 8 Bit Image Storage | E0 |
| 82 | 1.2.840.10008.5.1.4.1.1.77.1.5.2 | Ophthalmic Photography 16 Bit Image Storage | E0 |
| 83 | 1.2.840.10008.5.1.4.1.1.77.1.5.3 | Stereometric Relationship Storage | E0 |
| 84 | 1.2.840.10008.5.1.4.1.1.77.1.5.4 | Ophthalmic Tomography Image Storage | E0 |
| 85 | 1.2.840.10008.5.1.4.1.1.77.1.6 | VL Whole Slide Microscopy Image Storage | E0 |
| 86 | 1.2.840.10008.5.1.4.1.1.77.2 | VL Multi-frame Image Storage - Trial | E0 |
| 87 | 1.2.840.10008.5.1.4.1.1.78.1 | Lensometry Measurements Storage | E0 |
| 88 | 1.2.840.10008.5.1.4.1.1.78.2 | Autorefraction Measurements Storage | E0 |
| 89 | 1.2.840.10008.5.1.4.1.1.78.3 | Keratometry Measurements Storage | E0 |
| 90 | 1.2.840.10008.5.1.4.1.1.78.4 | Subjective Refraction Measurements Storage | E0 |
| 91 | 1.2.840.10008.5.1.4.1.1.78.5 | Visual Acuity Measurements Storage | E0 |
| 92 | 1.2.840.10008.5.1.4.1.1.78.6 | Spectacle Prescription Report Storage | E0 |
| 93 | 1.2.840.10008.5.1.4.1.1.78.7 | Ophthalmic Axial Measurements Storage | E0 |
| 94 | 1.2.840.10008.5.1.4.1.1.78.8 | Intraocular Lens Calculations Storage | E0 |
| 95 | 1.2.840.10008.5.1.4.1.1.79.1 | Macular Grid Thickness and Volume Report Storage | E0 |
| 96 | 1.2.840.10008.5.1.4.1.1.8 | Standalone Overlay Storage | E0 |
| 97 | 1.2.840.10008.5.1.4.1.1.80.1 | Ophthalmic Visual Field Static Perimetry Measurements Storage | E0 |
| 98 | 1.2.840.10008.5.1.4.1.1.81.1 | Ophthalmic Thickness Map Storage | E0 |
| 99 | 1.2.840.10008.5.1.4.1.1.88.11 | Basic Text SR Storage | E0 |
| 100 | 1.2.840.10008.5.1.4.1.1.88.22 | Enhanced SR Storage | E0 |
| 101 | 1.2.840.10008.5.1.4.1.1.88.33 | Comprehensive SR Storage | E0 |
| 102 | 1.2.840.10008.5.1.4.1.1.88.34 | Comprehensive 3D SR Storage | E0 |
| 103 | 1.2.840.10008.5.1.4.1.1.88.40 | Procedure Log Storage | E0 |
| 104 | 1.2.840.10008.5.1.4.1.1.88.50 | Mammography CAD SR Storage | E0 |
| 105 | 1.2.840.10008.5.1.4.1.1.88.59 | Key Object Selection Document Storage | E0 |
| 106 | 1.2.840.10008.5.1.4.1.1.88.65 | Chest CAD SR Storage | E0 |
| 107 | 1.2.840.10008.5.1.4.1.1.88.67 | X-Ray Radiation Dose SR Storage | E0 |
| 108 | 1.2.840.10008.5.1.4.1.1.88.69 | Colon CAD SR Storage | E0 |
| 109 | 1.2.840.10008.5.1.4.1.1.88.70 | Implantation Plan SR Storage | E0 |
| 110 | 1.2.840.10008.5.1.4.1.1.9 | Standalone Curve Storage | E0 |
| 111 | 1.2.840.10008.5.1.4.1.1.9.1.1 | 12-lead ECG Waveform Storage | E0 |
| 112 | 1.2.840.10008.5.1.4.1.1.9.1.2 | General ECG Waveform Storage | E0 |
| 113 | 1.2.840.10008.5.1.4.1.1.9.1.3 | Ambulatory ECG Waveform Storage | E0 |
| 114 | 1.2.840.10008.5.1.4.1.1.9.2.1 | Hemodynamic Waveform Storage | E0 |
| 115 | 1.2.840.10008.5.1.4.1.1.9.3.1 | Cardiac Electrophysiology Waveform Storage | E0 |
| 116 | 1.2.840.10008.5.1.4.1.1.9.4.1 | Basic Voice Audio Waveform Storage | E0 |
| 117 | 1.2.840.10008.5.1.4.1.1.9.4.2 | General Audio Waveform Storage | E0 |
| 118 | 1.2.840.10008.5.1.4.1.1.9.5.1 | Arterial Pulse Waveform Storage | E0 |
| 119 | 1.2.840.10008.5.1.4.1.1.9.6.1 | Respiratory Waveform Storage | E0 |
| 120 | 1.2.840.10008.5.1.4.34.7 | RT Beams Delivery Instruction Storage | E0 |
