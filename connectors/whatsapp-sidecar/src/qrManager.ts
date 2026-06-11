import QRCode from "qrcode";

export async function qrDataUrl(qr: string | null | undefined): Promise<string | null> {
  if (!qr) return null;
  return QRCode.toDataURL(qr, {
    errorCorrectionLevel: "M",
    margin: 1,
    scale: 6
  });
}
