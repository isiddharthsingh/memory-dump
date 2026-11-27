import AVFoundation
import Speech

/// Speech to text for the Ask box: en-US, partial results, stops at the end of
/// the utterance. The same settings as the Expo app's speech module. On the
/// iPhone whenever the recognizer can; otherwise Apple's servers hear it.
@Observable
final class VoiceInput {
  /// Why listening didn't start, for the Ask screen to say.
  enum Problem {
    /// The microphone or speech recognition was turned down, in Settings.
    case denied
    /// Recognition or the microphone can't run now (no network for a
    /// recognizer that needs it, or another app holding the audio).
    case unavailable

    var message: String {
      switch self {
      case .denied:
        "To ask by voice, allow Memory Dump to use the microphone and speech recognition in Settings."
      case .unavailable:
        "Voice input isn’t available right now. Try again in a moment, or type your question."
      }
    }
  }

  private(set) var isListening = false
  /// Set when a start fails; the Ask screen shows it and clears it.
  var problem: Problem?
  var onText: ((String) -> Void)?

  private let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
  private let engine = AVAudioEngine()
  private var request: SFSpeechAudioBufferRecognitionRequest?
  private var task: SFSpeechRecognitionTask?

  func toggle() async {
    if isListening { stop() } else { await start() }
  }

  func start() async {
    guard await Self.authorized() else {
      problem = .denied
      return
    }
    guard let recognizer, recognizer.isAvailable else {
      problem = .unavailable
      return
    }
    do {
      let session = AVAudioSession.sharedInstance()
      try session.setCategory(.record, mode: .measurement, options: .duckOthers)
      try session.setActive(true, options: .notifyOthersOnDeactivation)
    } catch {
      problem = .unavailable
      return
    }
    let request = SFSpeechAudioBufferRecognitionRequest()
    request.shouldReportPartialResults = true
    // Keeps the audio on the phone wherever the language's model is on it.
    if recognizer.supportsOnDeviceRecognition { request.requiresOnDeviceRecognition = true }
    self.request = request

    let input = engine.inputNode
    input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0), block: Self.tap(request))
    engine.prepare()
    do {
      try engine.start()
    } catch {
      stop()
      problem = .unavailable
      return
    }
    isListening = true
    Haptics.press()
    task = recognizer.recognitionTask(
      with: request,
      resultHandler: Self.results { [weak self] text, isFinal in
        if let text { self?.onText?(text) }
        if isFinal { self?.stop() }
      })
  }

  func stop() {
    guard request != nil else { return }
    engine.stop()
    engine.inputNode.removeTap(onBus: 0)
    request?.endAudio()
    task?.finish()
    request = nil
    task = nil
    isListening = false
    try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
  }

  // The audio tap and the recognizer call back on their own queues, so these
  // closures are made outside the main actor and hop back to it.

  private nonisolated static func tap(_ request: SFSpeechAudioBufferRecognitionRequest) -> AVAudioNodeTapBlock {
    nonisolated(unsafe) let request = request
    return { buffer, _ in request.append(buffer) }
  }

  private nonisolated static func results(
    _ update: @escaping @Sendable @MainActor (String?, Bool) -> Void
  ) -> @Sendable (SFSpeechRecognitionResult?, (any Error)?) -> Void {
    { result, error in
      let text = result?.bestTranscription.formattedString
      let isFinal = result?.isFinal == true || error != nil
      Task { @MainActor in update(text, isFinal) }
    }
  }

  private nonisolated static func authorized() async -> Bool {
    let speech = await withCheckedContinuation { (c: CheckedContinuation<Bool, Never>) in
      SFSpeechRecognizer.requestAuthorization { @Sendable status in c.resume(returning: status == .authorized) }
    }
    guard speech else { return false }
    return await AVAudioApplication.requestRecordPermission()
  }
}
