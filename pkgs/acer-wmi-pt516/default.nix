# Build the pdx86 Acer driver plus the exported patches as acer_wmi_pt516.
# modules/hardware/acer-wmi.nix passes the vendored base file and the
# patches, and blacklists the stock module. Remove this standalone build once
# the selected kernel includes the series (maintainers/README.md).
{
  lib,
  stdenv,
  kernel,
  source,
  patches,
  patchutils,
}:

stdenv.mkDerivation {
  pname = "acer-wmi-pt516";
  inherit (kernel) version;

  src = source;
  inherit patches;

  unpackPhase = "cp $src acer-wmi.c";
  postUnpack = "";
  # Preserve full upstream exports (including Kconfig/ABI docs), but only
  # apply the driver changes to this standalone module's one source file.
  patchPhase = ''
    for p in $patches; do
      filterdiff --include='*/drivers/platform/x86/acer-wmi.c' "$p" > module.patch
      if [ -s module.patch ]; then
        patch -p1 acer-wmi.c < module.patch
      fi
    done
    mv acer-wmi.c acer-wmi-pt516.c
    printf 'obj-m := acer-wmi-pt516.o\n' > Makefile
  '';

  hardeningDisable = [
    "pic"
    "format"
  ];
  nativeBuildInputs = kernel.moduleBuildDependencies ++ [ patchutils ];

  makeFlags = (kernel.moduleMakeFlags or [ ]) ++ [
    "-C"
    "${kernel.dev}/lib/modules/${kernel.modDirVersion}/build"
    "M=$(PWD)"
  ];
  buildFlags = [ "modules" ];
  installFlags = [ "INSTALL_MOD_PATH=$(out)" ];
  installTargets = [ "modules_install" ];

  meta = {
    description = "acer-wmi with the PT516-52s series, built out of tree as acer_wmi_pt516";
    license = lib.licenses.gpl2Plus;
    platforms = [ "x86_64-linux" ];
  };
}
