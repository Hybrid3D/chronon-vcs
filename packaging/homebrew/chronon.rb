# Homebrew formula for Chronon.
#
# This file is the source of truth; the published copy lives in the tap
# repository Hybrid3D/homebrew-tap as Formula/chronon.rb. See README.md in this
# directory for the release procedure.
class Chronon < Formula
  include Language::Python::Virtualenv

  desc "Local, document-oriented immutable history indexed by time"
  homepage "https://github.com/Hybrid3D/chronon-vcs"
  # Placeholder until the first PyPI release. Replace both lines with the hashed
  # "Source" URL and checksum from https://pypi.org/project/chronon-vcs/#files;
  # `brew style` flags this URL form until that is done.
  url "https://files.pythonhosted.org/packages/source/c/chronon-vcs/chronon_vcs-0.2.1.tar.gz"
  sha256 "0" * 64
  license "MIT"

  depends_on "python@3.13"

  # Dependency resources are generated, never hand-written. After bumping `url`
  # and `sha256`, regenerate them with:
  #
  #   brew update-python-resources Formula/chronon.rb
  #
  # That command resolves filelock, jsonschema, mcp, PyYAML, typer, and their
  # transitive dependencies from PyPI and rewrites the block below.
  # BEGIN generated resources
  # END generated resources

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/chronon --version")

    system bin/"chronon", "init", testpath/"vault"
    assert_path_exists testpath/"vault/.chronon/config.toml"

    (testpath/"vault/note.yml").write "title: hello\n"
    system bin/"chronon", "add", testpath/"vault/note.yml"
    assert_match "note.yml", shell_output("#{bin}/chronon status #{testpath}/vault/note.yml")
  end
end
