# netadopt

Reads a network-automation repository, says what is in it, and writes the
repository's model as Kubernetes objects. It reads *repositories*, never devices.

```
netadopt avd report ./repo --playbook build.yml
netadopt avd emit   ./repo --playbook build.yml > fabric.yaml
```

`report` names the Ansible it uses, the inventory Ansible resolves, and the plays as
written. `emit` writes one `Fabric` and a `FabricInput` for each group and host that
carries vars: the repository's own inventory, play and vars, unchanged.

It runs `ansible-inventory` in the repository and never connects to a device. Bring
your own Ansible, or:

```
uvx "netadopt[ansible]" avd report ./repo
```

## Where this is going

The model stays the vendor's: netadopt adopts an existing ecosystem into Kubernetes,
never a generic network model. Arista AVD is the first.

Next: proof on your own repository. Rebuild it from the objects, let Ansible resolve
both, and name what could not be carried.

## License

[Apache-2.0](LICENSE)
