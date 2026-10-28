// Raises every Pods target's IPHONEOS_DEPLOYMENT_TARGET to the app's own target.
// react_native_post_install only bumps pod native targets, so resource-bundle targets
// (e.g. SDWebImage-SDWebImage) keep their podspec's iOS 9.0, which Xcode 27 rejects.
const { withPodfile } = require('expo/config-plugins');

const MARKER = '# @generated withPodDeploymentTarget';

const SNIPPET = `    ${MARKER}
    app_deployment_target = installer.aggregate_targets.first.platform.deployment_target.to_s
    installer.pods_project.targets.each do |target|
      target.build_configurations.each do |build_config|
        current = build_config.build_settings['IPHONEOS_DEPLOYMENT_TARGET']
        if current.nil? || Gem::Version.new(current) < Gem::Version.new(app_deployment_target)
          build_config.build_settings['IPHONEOS_DEPLOYMENT_TARGET'] = app_deployment_target
        end
      end
    end
`;

// Matches an existing `post_install do |installer| ... end` block up to its closing `end`.
const POST_INSTALL = /^([ \t]*)post_install do \|installer\|\n[\s\S]*?^\1end$/m;

module.exports = function withPodDeploymentTarget(config) {
  return withPodfile(config, (cfg) => {
    const podfile = cfg.modResults.contents;
    if (podfile.includes(MARKER)) return cfg;

    if (POST_INSTALL.test(podfile)) {
      cfg.modResults.contents = podfile.replace(POST_INSTALL, (block, indent) => {
        const body = block.slice(0, block.length - `${indent}end`.length);
        return `${body}${SNIPPET}${indent}end`;
      });
    } else {
      cfg.modResults.contents = `${podfile}\npost_install do |installer|\n${SNIPPET}end\n`;
    }
    return cfg;
  });
};
