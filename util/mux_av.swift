#!/usr/bin/env swift

import AVFoundation
import Foundation

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

if CommandLine.arguments.count != 4 {
    fail("usage: mux_av.swift <video_file> <audio_file> <output_file>")
}

let videoURL = URL(fileURLWithPath: CommandLine.arguments[1])
let audioURL = URL(fileURLWithPath: CommandLine.arguments[2])
let outputURL = URL(fileURLWithPath: CommandLine.arguments[3])

let fileManager = FileManager.default
if fileManager.fileExists(atPath: outputURL.path) {
    do {
        try fileManager.removeItem(at: outputURL)
    } catch {
        fail("failed to remove existing output: \(error)")
    }
}

let videoAsset = AVURLAsset(url: videoURL)
let audioAsset = AVURLAsset(url: audioURL)

guard let sourceVideoTrack = videoAsset.tracks(withMediaType: .video).first else {
    fail("no video track found in \(videoURL.path)")
}

guard let sourceAudioTrack = audioAsset.tracks(withMediaType: .audio).first else {
    fail("no audio track found in \(audioURL.path)")
}

let composition = AVMutableComposition()

guard let compositionVideoTrack = composition.addMutableTrack(
    withMediaType: .video,
    preferredTrackID: kCMPersistentTrackID_Invalid
) else {
    fail("failed to create composition video track")
}

guard let compositionAudioTrack = composition.addMutableTrack(
    withMediaType: .audio,
    preferredTrackID: kCMPersistentTrackID_Invalid
) else {
    fail("failed to create composition audio track")
}

do {
    try compositionVideoTrack.insertTimeRange(
        CMTimeRange(start: .zero, duration: videoAsset.duration),
        of: sourceVideoTrack,
        at: .zero
    )
    compositionVideoTrack.preferredTransform = sourceVideoTrack.preferredTransform

    try compositionAudioTrack.insertTimeRange(
        CMTimeRange(start: .zero, duration: audioAsset.duration),
        of: sourceAudioTrack,
        at: .zero
    )
} catch {
    fail("failed to build composition: \(error)")
}

guard let exporter = AVAssetExportSession(asset: composition, presetName: AVAssetExportPresetPassthrough) else {
    fail("failed to create export session")
}

let outputType: AVFileType = exporter.supportedFileTypes.contains(.mp4) ? .mp4 : .mov
exporter.outputURL = outputURL
exporter.outputFileType = outputType
exporter.shouldOptimizeForNetworkUse = true

let semaphore = DispatchSemaphore(value: 0)
exporter.exportAsynchronously {
    semaphore.signal()
}
semaphore.wait()

switch exporter.status {
case .completed:
    exit(0)
case .failed:
    fail("export failed: \(exporter.error?.localizedDescription ?? "unknown error")")
case .cancelled:
    fail("export cancelled")
default:
    fail("export ended with status \(exporter.status.rawValue)")
}
