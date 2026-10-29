import SwiftUI
import UIKit

/// Entry point named in Info.plist (NSExtensionPrincipalClass). Hosts the
/// SwiftUI pill and closes the extension when the model is done.
final class ShareViewController: UIViewController {
  private var model: ShareModel?

  override func viewDidLoad() {
    super.viewDidLoad()
    view.backgroundColor = .clear
    let items = extensionContext?.inputItems.compactMap { $0 as? NSExtensionItem } ?? []
    let model = ShareModel(items: items) { [weak self] in
      self?.extensionContext?.completeRequest(returningItems: nil)
    }
    self.model = model

    let host = UIHostingController(rootView: ShareView(model: model))
    host.view.backgroundColor = .clear
    addChild(host)
    host.view.frame = view.bounds
    host.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
    view.addSubview(host.view)
    host.didMove(toParent: self)
    model.start()
  }

  /// The pill drops in only once the presentation has finished, so it never
  /// rides along with the system's presentation animation.
  override func viewDidAppear(_ animated: Bool) {
    super.viewDidAppear(animated)
    model?.shown = true
  }
}
