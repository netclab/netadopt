# netadopt

Reads a network-automation repository, says what is in it, and writes the
repository's model as Kubernetes resources. It reads *repositories*, never devices.

Arista AVD first: netadopt uses AVD's own data model - no new schema, no translation.
The objects carry the repository's inventory, play and vars exactly as written.

## Installing

With [uv](https://docs.astral.sh/uv/):

```
uv tool install netadopt
```

or, to run it once without installing:

```
uvx netadopt avd report ./repo --playbook build.yml
```

netadopt uses the Ansible already on `PATH`. On a machine without one, the `ansible`
extra brings ansible-core along:

```
uv tool install "netadopt[ansible]"
```

```
uvx "netadopt[ansible]" avd report ./repo --playbook build.yml
```

## Reading an AVD repository

Run on AVD's `single-dc-l3ls` example:

```
$ netadopt avd report single-dc-l3ls --playbook build.yml
single-dc-l3ls

Ansible     ansible-core 2.21.3    ~/.local/bin/ansible-playbook
Config      ansible.cfg
Inventory   named by ansible.cfg   8 hosts, 8 groups
Variables   8 files                group_vars 8, host_vars 0
Playbook    build.yml              1 play

#   Play                                     Hosts    Roles
─────────────────────────────────────────────────────────────────────────────────────
0   Build Configurations and Documentation   FABRIC   eos_designs, eos_cli_config_gen

Carried
  Fabric         single-dc-l3ls, from play 0
  FabricInputs   7

Warnings
  ansible_password   plain text in FabricInput single-dc-l3ls-fabric, carried as the repository has it
```

- **Carried** is what `emit` writes.
- **Not carried** is what it leaves behind, each with the reason. On AVD's
  `cv-pathfinder` example, the vault password:

  ```
  Not carried
    .vault   the vault password; create its Secret:
      kubectl create secret generic cv-pathfinder-vault --from-file=password=cv-pathfinder/.vault
  ```

- **Warnings** are carried, but worth knowing: a password in plain text, a vars file
  for a host the inventory does not have.

Exit codes: `0` when the model is carried whole, warnings or not; `2` when a part of
it is not carried or something did not read; `1` when there is no Ansible at all.

## Writing Kubernetes manifests

```
$ netadopt avd emit single-dc-l3ls --playbook build.yml > single-dc-l3ls.yaml
```

One `Fabric` - the play, the inventory and `ansible.cfg`, as written - and one
`FabricInput` per group or host that carries vars:

```yaml
apiVersion: avd.netclab.dev/v1alpha1
kind: FabricInput
metadata:
  name: single-dc-l3ls-dc1
  labels:
    avd.netclab.dev/fabric: single-dc-l3ls
spec:
  appliesTo:
    group: DC1
  beside: inventory
  design:
    mgmt_gateway: 172.16.1.1
    management_eapi:
      enabled: true
```

Group and host names stay the repository's; only `metadata.name` is spelled for
Kubernetes. The YAML goes to stdout and everything said about it to stderr.

Apply them server-side:

```
kubectl apply --server-side -f single-dc-l3ls.yaml
```

A plain `kubectl apply` drops every `key: null` in a map, and AVD reads a null
differently from a missing key.

## Before running it on someone else's repository

`report` asks Ansible about the inventory, in the repository. Ansible then runs
whatever the repository gives it: an executable inventory, and the plugin directories
named in its `ansible.cfg`. `report` names both under Warnings - after running them.

`emit` runs no Ansible; it only reads files. Neither command renders Jinja, so a
`lookup('pipe', ...)` in the vars runs only where the design is rendered.

Neither connects to a device.

## Tested on AVD's own repositories

Each release is tested on the examples and test scenarios that ship with AVD, at the
release `netadopt --version` names. Every one that can be carried is emitted, rebuilt
from the objects, and resolved by Ansible on both sides, and every host must end up
with the same variables.

## Limits

- The inventory must be a YAML file. An executable or dynamic inventory is not
  carried.
- A code directory named in `ansible.cfg`, such as `vars_plugins = plugins/vars`, is not
  carried.
- One play per `Fabric`. Another play of the same playbook is another run:
  `--play N --name NAME`.

## Where this is going

- The same proof on your own repository, not only a report.
- A second ecosystem after AVD, under a subcommand of its own, with its model kept as
  its vendor writes it.

## License

[Apache-2.0](https://github.com/netclab/netadopt/blob/main/LICENSE)
