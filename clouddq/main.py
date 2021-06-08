# Copyright 2021 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""todo: add main docstring."""
import json
from pathlib import Path
from pprint import pprint
import typing

import click

from clouddq import lib
from clouddq import utils
from clouddq.bigquery_utils import validate_sql_string
from clouddq.utils import get_template_file


def not_null_or_empty(
    ctx: click.Context,
    param: typing.Union[click.core.Option, click.core.Parameter],
    value: typing.Any,
) -> typing.Any:
    if value:
        return value
    else:
        raise click.BadParameter(
            f"Variable {param} must not be empty or none. Input value: {value}"
        )


@click.command()
@click.argument("rule_binding_ids")
@click.argument(
    "rule_binding_config_path",
    type=click.Path(exists=True),
)
@click.option(
    "--dbt_profiles_dir",
    help="Path to dbt profiles.yml. "
    "This is a required argument for configuring dbt connection profiles."
    "Defaults to environment variable DBT_PROFILES_DIR if set.",
    type=click.Path(exists=True),
    envvar="DBT_PROFILES_DIR",
    callback=not_null_or_empty,
)
@click.option(
    "--dbt_path",
    help="Path to dbt model directory where a new view will be created "
    "containing the sql execution statement for each rule binding."
    "If not specified, clouddq will created a new directory in "
    "the current working directory for the dbt generated sql files.",
    type=click.Path(exists=True),
    default=None,
)
@click.option(
    "--environment_target",
    help="Execution environment target as defined in dbt profile, "
    "e.g. dev, test, prod.  "
    "Defaults 'dev' or the environment variable ENV if set.",
    envvar="ENV",
    default="dev",
    callback=not_null_or_empty,
)
@click.option(
    "--metadata",
    help="JSON String containing run metadata for each DQ Outputs row. "
    "Useful for allowing custom aggregations over DQ Outputs table "
    "over the metadata attributes.",
    default="{}",
)
@click.option(
    "--dry_run",
    help="If True, do everything except run dbt itself.",
    is_flag=True,
    default=False,
)
@click.option(
    "--debug",
    help="If True, print additional diagnostic information.",
    is_flag=True,
    default=False,
)
@click.option(
    "--print_sql_queries",
    help="If True, print generated SQL queries to stdout.",
    is_flag=True,
    default=False,
)
@click.option(
    "--skip_sql_validation",
    help="If True, skip validation step of generated SQL using BigQuery dry-run.",
    is_flag=True,
    default=False,
)
@click.option(
    "--progress_watermark",
    help="Whether to set 'progress_watermark' column value "
    "to True/False in dq_summary. Defaults to True.",
    type=bool,
    default=True,
)
def main(  # noqa: C901
    rule_binding_ids: str,
    rule_binding_config_path: str,
    dbt_path: str,
    dbt_profiles_dir: str,
    environment_target: str,
    metadata: str,
    dry_run: bool,
    progress_watermark: bool,
    debug: bool = False,
    print_sql_queries: bool = False,
    skip_sql_validation: bool = False,
) -> None:
    """Run RULE_BINDING_IDS from a RULE_BINDING_CONFIG_PATH.

    RULE_BINDING_IDS:
    comma-separated Rule Binding ID(s) containing the
    configurations for the run.

    Set RULE_BINDING_IDS to 'ALL' to run all rule_bindings
    in RULE_BINDING_CONFIG_PATH.

    RULE_BINDING_CONFIG_PATH:
    Path to YAML configs directory containing `rule_bindings`,
    `entities`, `rules`, and `row_filters` YAML config files.

    Usage examples:

    > python clouddq \\\n
      T2_DQ_1_EMAIL \\\n
      configs/rule_bindings/team-2-rule-bindings.yml \\\n
      --dbt_profiles_dir=. \\\n
      --metadata='{"test":"test"}' \\\n

    > python bazel-bin/clouddq/clouddq_patched.zip \\\n
      ALL \\\n
      configs/ \\\n
      --metadata='{"test":"test"}' \\\n
      --dbt_profiles_dir=. \\\n
      --dbt_path=dbt \\\n
      --environment_target=dev

    """
    if debug:
        click.secho(f"Current working directory: {Path().cwd()}", fg="yellow")
    dbt_profiles_dir = Path(dbt_profiles_dir).absolute()
    if not dbt_profiles_dir.joinpath("profiles.yml").is_file():
        raise ValueError(
            f"Cannot find connection `profiles.yml` configurations at "
            f"`dbt_profiles_dir` path: {dbt_profiles_dir}"
        )
    if debug:
        click.secho(f"Using 'dbt_profiles_dir': {dbt_profiles_dir}", fg="yellow")
    if not dbt_path:
        dbt_path = Path().cwd().joinpath("dbt").absolute()
        if debug:
            click.secho(
                f"No argument 'dbt_path' provided. Defaulting to use "
                f"'dbt' directory in current working directory at: {dbt_path}",
                fg="magenta",
            )
    else:
        dbt_path = Path(dbt_path).absolute()
        if dbt_path.name != "dbt":
            dbt_path = dbt_path / "dbt"
    if not dbt_path.is_dir():
        if debug:
            click.secho(
                f"Creating a new dbt directory at 'dbt_path': {dbt_path}", fg="magenta"
            )
        dbt_path.mkdir(parents=True, exist_ok=True)
    if debug:
        click.secho(f"Using 'dbt_path': {dbt_path}", fg="yellow")
    dbt_project_path = dbt_path.absolute().joinpath("dbt_project.yml")
    if not dbt_project_path.is_file():
        if debug:
            click.secho(
                f"Cannot find `dbt_project.yml` configurations in current path: "
                f"{dbt_project_path}",
                fg="magenta",
            )
            click.secho(
                f"Writing templated 'dbt_project.yml' to: {dbt_project_path} ",
                fg="magenta",
            )
        dbt_project_path.write_text(get_template_file(Path("dbt", "dbt_project.yml")))
    click.secho(f"Using 'dbt_project_path': {dbt_project_path}", fg="yellow")
    dbt_main_path = dbt_path / "models" / "data_quality_engine"
    dbt_main_path.mkdir(parents=True, exist_ok=True)
    if not dbt_main_path.joinpath("main.sql").is_file():
        dbt_main_path.joinpath("main.sql").write_text(
            get_template_file(
                Path("dbt").joinpath("models", "data_quality_engine", "main.sql")
            )
        )
    if not dbt_main_path.joinpath("dq_summary.sql").is_file():
        dbt_main_path.joinpath("dq_summary.sql").write_text(
            get_template_file(
                Path("dbt").joinpath("models", "data_quality_engine", "dq_summary.sql")
            )
        )
    dbt_rule_binding_views_path = dbt_path / "models" / "rule_binding_views"
    dbt_rule_binding_views_path.mkdir(parents=True, exist_ok=True)
    click.secho(
        f"Writing generated sql to {dbt_rule_binding_views_path.absolute()}/",
        fg="yellow",
    )
    configs_path = Path(rule_binding_config_path)
    if debug:
        click.secho(
            f"Loading rule bindings from: {configs_path.absolute()} ", fg="yellow"
        )
    metadata = json.loads(metadata)
    dq_summary_table_name = lib.get_bigquery_dq_summary_table_name(
        dbt_profiles_dir, environment_target, dbt_project_path
    )
    click.secho(
        f"Writing summary results to BigQuery table: `{dq_summary_table_name}`. ",
        fg="yellow",
    )
    all_rule_bindings = lib.load_rule_bindings_config(Path(configs_path))
    target_rule_binding_ids = [r.strip() for r in rule_binding_ids.split(",")]
    if len(target_rule_binding_ids) == 1 and target_rule_binding_ids[0] == "ALL":
        target_rule_binding_ids = list(all_rule_bindings.keys())
    (
        entities_collection,
        row_filters_collection,
        rules_collection,
    ) = lib.load_configs_if_not_defined(
        configs_path=configs_path,
    )
    for rule_binding_id in target_rule_binding_ids:
        rule_binding_configs = all_rule_bindings.get(rule_binding_id, None)
        utils.assert_not_none_or_empty(
            rule_binding_configs,
            f"Target Rule Binding Id: {rule_binding_id} not found "
            f"in config path {configs_path.absolute()}.",
        )
        if debug:
            click.secho(
                f"Creating sql string from configs for rule binding: {rule_binding_id}",
                fg="cyan",
            )
            click.secho("Rule binding config json:", fg="cyan")
            pprint(rule_binding_configs)
        sql_string = lib.create_rule_binding_view_model(
            rule_binding_id=rule_binding_id,
            rule_binding_configs=rule_binding_configs,
            dq_summary_table_name=dq_summary_table_name,
            entities_collection=entities_collection,
            rules_collection=rules_collection,
            row_filters_collection=row_filters_collection,
            configs_path=configs_path,
            environment=environment_target,
            metadata=metadata,
            debug=print_sql_queries,
            progress_watermark=progress_watermark,
        )
        if not skip_sql_validation:
            validate_sql_string(sql_string)
        if debug:
            click.secho(
                f"*** Writing sql to {dbt_rule_binding_views_path.absolute()}/"
                f"{rule_binding_id}.sql",
                fg="cyan",
            )
        lib.write_sql_string_as_dbt_model(
            rule_binding_id=rule_binding_id,
            sql_string=sql_string,
            dbt_rule_binding_views_path=dbt_rule_binding_views_path,
        )
    # clean up old rule_bindings
    for view in dbt_rule_binding_views_path.glob("*.sql"):
        if view.stem not in target_rule_binding_ids:
            view.unlink()
    configs = {"target_rule_binding_ids": target_rule_binding_ids}
    try:
        click.secho(f"Running dbt in path: {dbt_path}", fg="yellow")
        utils.run_dbt(
            dbt_path=dbt_path,
            dbt_profile_dir=dbt_profiles_dir,
            configs=configs,
            environment=environment_target,
            debug=debug,
            dry_run=dry_run,
        )
    except Exception as e:
        click.secho("Encountered error: " + str(e), fg="red")


if __name__ == "__main__":
    main()
