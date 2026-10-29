import SwiftUI
import UIKit

/// Entry point named in Info.plist (NSExtensionPrincipalClass). Hosts the
/// SwiftUI card and closes the sheet when the model is done.
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
}
