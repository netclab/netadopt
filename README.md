# netadopt

Reads a network-automation repository, says what is in it, and writes the
repository's model as Kubernetes resources. It reads *repositories*, never devices.

Arista AVD first: netadopt uses AVD's own data model - no new schema, no translation.
The objects carry the repository's inventory, play and vars exactly as written.

## Installing

With [uv](https://docs.astral.sh/uv/), to run it once without installing:

```
uvx "netadopt[avd]" avd report ./repo --playbook build.yml
```

or to install it:

```
uv tool install "netadopt[avd]"
```

The `avd` extra installs everything netadopt needs to run AVD: ansible-core, pyavd,
and the two libraries AVD requires with them, distlib and netaddr. pyavd is pinned to
the AVD version shown by `netadopt --version`. netadopt uses only this Ansible; one
already installed elsewhere on the machine is not used.

## Reading an AVD repository

Run on AVD's `single-dc-l3ls` example:

```
$ netadopt avd report single-dc-l3ls --playbook build.yml
single-dc-l3ls

Ansible     ansible-core 2.21.4    ~/.local/share/uv/tools/netadopt/bin/ansible-playbook
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
- **Renders** is not measured here. Rendering runs everything the vars hold and needs
  AVD's collections, so it belongs to `netadopt avd lab`, and the row is the command
  that measures it. A report says the model was read, never that it builds.

Exit codes: `0` when the model is carried whole, warnings or not; `2` when a part of
it is not carried or something did not read; `1` when netadopt was installed without
the `avd` extra, and then nothing else is reported.

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

## Building a lab from the fabric

`lab` writes a lab topology of the same fabric: the cabling, as nodes and networks.

```
$ netadopt avd lab single-dc-l3ls --playbook build.yml > values.yaml
rendering play [0] of build.yml with arista.avd.eos_designs
rendered 8 hosts
10 nodes (8 ceos, 2 linux), 22 networks
```

The values are [netclab-chart](https://github.com/netclab/netclab-chart)'s:

```yaml
topology:
  networks:
  - name: n1
  - name: n2
  # ...
  nodes:
  - name: dc1-leaf1-server1
    type: linux
    interfaces:
    - name: ilo
      network: n1
    - name: pci1
      network: n2
    - name: pci2
      network: n3
  - name: dc1-leaf1a
    type: ceos
    interfaces:
    - name: eth1
      network: n4
    # ...
```

```
helm install lab netclab/netclab --values values.yaml
```

The nodes are cabled and empty: no configuration is loaded into them. The cabling is
AVD's, so netadopt renders the repository with AVD to get it - on a copy, never in the
repository, and with the collections it pins, fetched into its own cache the first time
and never again.

That render is also the answer `report` cannot give: once a run reaches `rendered N
hosts`, the repository builds on the AVD version shown by `netadopt --version`. A run
that stops instead prints what AVD refused, host by host.

A peer outside the fabric - a server, a firewall - becomes the node `--connected`
says: `linux` by default, or `ceos`, or `none` to leave it out. `--ceos-image`,
`--ceos-memory` and `--ceos-cpu` are written only when given, so the chart's own
defaults hold otherwise.

Node names are the repository's hostnames, lowercased for Kubernetes. A host no name
can be spelled from is left out with its cables and said on stderr; two hosts that
would share a name stop the lab rather than one of them being guessed at.

Exit codes are `report`'s: `0`, or `2` when there is no lab, or `1` without the `avd`
extra.

## Before running it on someone else's repository

`report` asks Ansible about the inventory, in the repository. Ansible then runs
whatever the repository gives it: an executable inventory, and the plugin directories
named in its `ansible.cfg`. `report` names both under Warnings - after running them.

`emit` runs no Ansible; it only reads files. Neither renders Jinja.

`lab` does render it, with AVD, so everything in the vars runs - a `lookup('pipe',
...)` included. It renders on a copy of the repository, never in it, and it is the one
command that reaches the network itself: on its first run, to fetch the collections it
pins.

None of them connects to a device.

## Tested on AVD's own repositories

Each release is tested on the examples and test scenarios that ship with AVD, at the
version shown by `netadopt --version`. Every one that can be carried is emitted, rebuilt
from the objects, and resolved by Ansible on both sides, and every host must end up
with the same variables. Every one that renders is also built into a lab, where each
cable AVD wrote has to reach the topology or be named as left out.

## Limits

- The inventory must be a YAML file. An executable or dynamic inventory is not
  carried.
- A code directory named in `ansible.cfg`, such as `vars_plugins = plugins/vars`, is not
  carried.
- One play per `Fabric`. Another play of the same playbook is another run:
  `--play N --name NAME`.
- `lab` cables `EthernetN` only. A subinterface rides on its parent's cable, and a
  breakout such as `Ethernet1/4` is left out and named.
- netclab-chart names a veth `<release>-<network>-<hash>` within the 15 bytes Linux
  allows, and refuses to render when there is no room left, so keep the Helm release
  name short: nine characters fits every lab up to `n99`.

## Where this is going

- The same proof on your own repository, not only a report.
- `lab --for containerlab`, beside netclab-chart.
- A second ecosystem after AVD, under a subcommand of its own, with its model kept as
  its vendor writes it.

## Developing

Running the checks, the two test tiers and the release:
[DEVELOPMENT.md](https://github.com/netclab/netadopt/blob/main/DEVELOPMENT.md).

## License

[Apache-2.0](https://github.com/netclab/netadopt/blob/main/LICENSE)
