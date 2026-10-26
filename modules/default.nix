# All three layers. Importing enables nothing: set pt516.hardware.enable,
# pt516.turbo.broker.enable and services.performance-policy.enable as
# needed (docs/configuration.md).
{
  imports = [
    ./hardware
    ./broker
    ./policy
  ];
}
