import Foundation
import Vision
import AppKit
import PDFKit

guard CommandLine.arguments.count >= 2 else { exit(1) }
let url = URL(fileURLWithPath: CommandLine.arguments[1])
func recognize(_ handler: VNImageRequestHandler) throws -> String {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.recognitionLanguages = ["ru-RU", "en-US"]
    request.usesLanguageCorrection = true
    try handler.perform([request])
    let lines = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
    return lines.joined(separator: "\n")
}
do {
    if url.pathExtension.lowercased() == "pdf" {
        guard let document = PDFDocument(url:url) else { exit(2) }
        if document.isLocked {
            print("{\"locked\":true,\"pages\":[]}"); exit(0)
        }
        let requested = CommandLine.arguments.count > 2 ? (Int(CommandLine.arguments[2]) ?? 4) : 4
        let limit = min(document.pageCount, max(2,min(4,requested)))
        var pages: [[String:Any]] = []
        for index in 0..<limit {
            guard let page = document.page(at:index) else { continue }
            var text = page.string ?? ""
            var method = "text"
            if text.filter({$0.isLetter}).count < 40 {
                let bounds = page.bounds(for:.mediaBox)
                let scale = min(2.5,1800 / max(bounds.width,bounds.height))
                let image = page.thumbnail(of:NSSize(width:bounds.width*scale,height:bounds.height*scale),for:.mediaBox)
                var rect = NSRect(origin:.zero,size:image.size)
                if let cg = image.cgImage(forProposedRect:&rect,context:nil,hints:nil) {
                    text = try recognize(VNImageRequestHandler(cgImage:cg,options:[:]))
                    method = "ocr"
                }
            }
            pages.append(["number":index+1,"text":String(text.prefix(14000)),"method":method])
        }
        let data = try JSONSerialization.data(withJSONObject:["total_pages":document.pageCount,"pages":pages])
        print(String(data:data,encoding:.utf8)!)
    } else {
        print(try recognize(VNImageRequestHandler(url:url,options:[:])))
    }
} catch { exit(2) }
