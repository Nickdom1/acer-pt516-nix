{
  description = "NixOS integration for the Acer Predator PT516-52s: acer-wmi fixes, an optional Turbo broker and an optional session policy";

  inputs = {
    # Reference package set (nixos-26.05). Used only for this flake's checks,
    # packages and example configurations. Importing nixosModules never uses
    # it: the modules build with the consumer's own pkgs and kernel.
    nixpkgs.url = "github:nixos/nixpkgs/ecc58f32d1069a3e3f8a991e2233d365689f748d";
    # Reference kernel set: linuxPackages_latest = 7.2.7 with NVIDIA open
    # 595.99.02, the combination with hardware acceptance. Examples and the
    # inspection package only.
    nixpkgs-kernel.url = "github:nixos/nixpkgs/58a20b0a882fffd508ca4dad73836d76632792a3";
  };

  outputs =
    {
      self,
      nixpkgs,
      nixpkgs-kernel,
    }:
    let
      system = "x86_64-linux";
      inherit (nixpkgs) lib;
      pkgs = nixpkgs.legacyPackages.${system};
      kernelPackages = nixpkgs-kernel.legacyPackages.${system}.linuxPackages_latest;
      source =
        paths:
        lib.fileset.toSource {
          root = ./.;
          fileset = lib.fileset.unions paths;
        };

      # Example configurations as a consumer would write them, plus a stub
      # for the parts that come from the consumer's own hardware file.
      example =
        modules:
        lib.nixosSystem {
          inherit system;
          modules = modules ++ [
            ./examples/stub-hardware.nix
            # The example's choice of kernel: the tested reference.
            { boot.kernelPackages = kernelPackages; }
          ];
        };
    in
    {
      nixosModules = {
        hardware = ./modules/hardware;
        broker = ./modules/broker;
        policy = ./modules/policy;
        default = ./modules;
      };

      packages.${system} = {
        turbo-broker = pkgs.callPackage ./pkgs/turbo-broker { };
        pt516-check = pkgs.callPackage ./pkgs/pt516-check { };
        # The hardware module's default package for the reference kernel.
        acer-wmi-pt516 = self.nixosConfigurations.example-minimal.config.pt516.hardware.acerWmi.package;
      };

      nixosConfigurations = {
        example-minimal = example [
          self.nixosModules.hardware
          ./examples/minimal/configuration.nix
        ];
        example-broker-policy = example [
          self.nixosModules.hardware
          self.nixosModules.broker
          self.nixosModules.policy
          ./examples/broker-policy/configuration.nix
        ];
      };

      checks.${system} = {
        unit-tests =
          pkgs.runCommand "acer-pt516-nix-unit-tests"
            {
              src = source [
                ./tests
                ./pkgs
              ];
              nativeBuildInputs = [ pkgs.python3 ];
            }
            ''
              cp -r $src/. . && chmod -R u+w .
              python3 -m unittest discover -s tests -p 'test_*.py' -v
              touch $out
            '';
        # The actual driver functions, compiled against mocked WMI replies,
        # from the vendored base plus the exported patches. Also checks that
        # the result is the file at the series head recorded in SOURCE.
        acer-wmi-harness =
          pkgs.runCommandCC "acer-pt516-nix-acer-wmi-harness"
            {
              src = source [
                ./kernel/acer-wmi
                ./maintainers/kernel-tests
              ];
              nativeBuildInputs = [
                pkgs.python3
                pkgs.patchutils
              ];
            }
            ''
              cp -r $src/. . && chmod -R u+w .
              cp kernel/acer-wmi/base/acer-wmi.c acer-wmi.c
              for p in kernel/acer-wmi/patches/*.patch; do
                filterdiff --include='*/drivers/platform/x86/acer-wmi.c' "$p" > module.patch
                if [ -s module.patch ]; then patch -p1 acer-wmi.c < module.patch; fi
              done
              expected=$(sed -n 's/^acer-wmi\.c at head: sha256 //p' kernel/acer-wmi/SOURCE)
              actual=$(sha256sum acer-wmi.c | cut -c1-64)
              if [ "$expected" != "$actual" ]; then
                echo "patched acer-wmi.c is $actual; SOURCE records the series head as $expected" >&2
                exit 1
              fi
              export ACER_WMI_SOURCE=$PWD/acer-wmi.c TURBO_REQUIRE_GCC=1
              python3 maintainers/kernel-tests/test_acer_wmi_turbo.py -v
              python3 maintainers/kernel-tests/test_acer_wmi_timeout.py -v
              echo "$actual" > $out
            '';
        performance-policy = import ./tests/performance-policy.nix { inherit pkgs; };
        modules-eval = import ./tests/modules-eval.nix {
          inherit pkgs lib;
          inherit (lib) nixosSystem;
          modules = self.nixosModules;
        };
      };

      # Versions the author's suite ran on. Other combinations are untested.
      lib.tested = {
        model = "Predator PT516-52s";
        nixpkgs = "ecc58f32d1069a3e3f8a991e2233d365689f748d";
        kernelNixpkgs = "58a20b0a882fffd508ca4dad73836d76632792a3";
        kernel = "7.2.7";
        compileOnlyKernels = [ "6.18.52" ];
        nvidia = "595.99.02 (open kernel module)";
        powerProfilesDaemon = "0.30";
        thermald = "2.5.11";
      };
    };
}
