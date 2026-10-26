# The root hardware owner (turbo-broker daemon, status, on/off, game-profile,
# boot-reset). The source is one Python file with no dependencies beyond the
# standard library; see docs/architecture.md.
{ writers }:

writers.writePython3Bin "turbo-broker" {
  flakeIgnore = [ "E501" ];
} (builtins.readFile ./turbo-broker.py)
