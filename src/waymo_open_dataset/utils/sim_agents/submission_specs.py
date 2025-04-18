# Copyright 2023 The Waymo Open Dataset Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# =============================================================================
"""Specifications and validation utils for Sim Agents Challenge submissions."""
import dataclasses
import enum
from typing import Sequence

from waymo_open_dataset.protos import scenario_pb2
from waymo_open_dataset.protos import sim_agents_submission_pb2


class ChallengeType(enum.Enum):
  """Type of Waymo Open Dataset Challenge to evaluate.

  Right now, we have two challenges:
  - Sim Agents: The agent future states are resimulated, given the agent past
      and current states, traffic lights, and roadgraph.
  - Scenario Gen: The agent full states (past, current, and future) are
      resimulated from scratch, givn the roadgraph and traffic lights.

  """

  SIM_AGENTS = 'sim_agents'
  SCENARIO_GEN = 'scenario_gen'



@dataclasses.dataclass(frozen=True)
class SubmissionConfig:
  """Configuration for a valid submission."""

  # Index of the `current` step (11th when 1-indexed).
  # See https://waymo.com/open/data/motion/ for more info.
  current_time_index: int
  # Number of steps of simulation for a valid submission.
  n_simulation_steps: int
  # Number of parallel rollouts required for a valid submission.
  n_rollouts: int
  # Duration (in seconds) of each step of simulation. This corresponds to the
  # 10Hz frequency of the original Scenarios.
  step_duration_seconds: float

  def is_valid_sim_agent(self, track: scenario_pb2.Track) -> bool:
    """Checks if the object needs to be resimulated as a sim agent.

    For both Sim Agents and Scenario Gen challenge, every object that is valid
    at the `current_time_index` step (here hardcoded to 10) needs to be
    resimulated.

    Args:
      track: A track proto for a single object.

    Returns:
      A boolean flag, True if the object needs to be resimulated, False
        otherwise.
    """
    return track.states[self.current_time_index].valid


_SIM_AGENTS_SUBMISSION_CONFIG = SubmissionConfig(
    current_time_index=10,
    # Number of steps of simulation for a valid submission, the same length as
    # the future states in the original scenario.
    n_simulation_steps=80,
    n_rollouts=32,
    step_duration_seconds=0.1,
)

_SCENARIO_GEN_SUBMISSION_CONFIG = SubmissionConfig(
    current_time_index=10,
    # Number of steps of simulation for a valid submission, the same length as
    # the full states in the original scenario.
    n_simulation_steps=91,
    n_rollouts=32,
    step_duration_seconds=0.1,
)




def get_submission_config(challenge_type: ChallengeType) -> SubmissionConfig:
  """Returns the submission config for the given challenge type."""
  if challenge_type == ChallengeType.SIM_AGENTS:
    return _SIM_AGENTS_SUBMISSION_CONFIG
  elif challenge_type == ChallengeType.SCENARIO_GEN:
    return _SCENARIO_GEN_SUBMISSION_CONFIG

  else:
    raise ValueError(f'Unknown {challenge_type=}')


def get_sim_agent_ids(
    scenario: scenario_pb2.Scenario, challenge_type: ChallengeType
) -> Sequence[int]:
  """Returns the list of object IDs that needs to be resimulated.

  Internally calls `is_valid_sim_agent` to verify the simulation criteria,
  i.e. is the object valid at `current_time_index`.

  Args:
    scenario: The Scenario proto containing the data.
    challenge_type: The challenge type to use.

  Returns:
    A list of int IDs, containing all the objects that need to be simulated.
  """
  object_ids = []
  config = get_submission_config(challenge_type)
  for track in scenario.tracks:
    if config.is_valid_sim_agent(track):
      object_ids.append(track.id)
  return object_ids


def get_evaluation_sim_agent_ids(
    scenario: scenario_pb2.Scenario, challenge_type: ChallengeType
) -> Sequence[int]:
  """Returns the list of object IDs that are used for evaluation.

  The criteria to be evaluated is the same as the existing Behaviour Prediction
  challenges, i.e. the ID is included in the `tracks_to_predict` field. These
  agents are usually the most relevant and the least noisy, so we restrict the
  evaluation to these objects to reduce the noise. We also always include the
  AV as a sim agent.

  The remaining sim agents, i.e. the ones that needs to be resimulated as
  specified by `is_valid_sim_agent` but not included into
  `get_evaluation_sim_agent_ids`, are not directly evaluated (e.g. using their
  speed as a distribution matching feature), but are still considered part of
  the simulation, so they affect the evaluated agent metrics. E.g. if an
  evaluated sim agent collides with one of these objects, a collision event
  will still be raised.

  Args:
    scenario: The Scenario proto containing the data.
    challenge_type: The challenge type to use.

  Returns:
    A list of int IDs, containing all the objects that will be used for
    evaluation. This list is guaranteed to have no repeated IDs.
  """
  # Start with the AV object.
  if challenge_type == ChallengeType.SIM_AGENTS:
    object_ids = {scenario.tracks[scenario.sdc_track_index].id}
    # Add the `tracks_to_predict` objects.
    for required_prediction in scenario.tracks_to_predict:
      object_ids.add(scenario.tracks[required_prediction.track_index].id)
    return sorted(object_ids)
  elif challenge_type == ChallengeType.SCENARIO_GEN:
    return get_sim_agent_ids(scenario, challenge_type)

  else:
    raise ValueError(f'Unknown {challenge_type=}')


def validate_joint_scene(
    joint_scene: sim_agents_submission_pb2.JointScene,
    original_scenario: scenario_pb2.Scenario,
    challenge_type: ChallengeType,
) -> None:
  """Validates a single `JointScene`.

  Checks the following properties:
  - All the objects that are valid at step 11th (when 1-indexed, same as
      `current_time_index`) are present in the scene.
  - All submission trajectories are 80-steps in length.

  Args:
    joint_scene: The `JointScene` to be validated.
    original_scenario: The `Scenario` proto from which the simulation was
      started.
    challenge_type: The challenge type to use.

  Raises:
    ValueError if the `JointScene` is not valid.
  """
  config = get_submission_config(challenge_type)
  # Enumerate all the object IDs that needs to be simulated.
  sim_agent_ids = get_sim_agent_ids(original_scenario, challenge_type)
  simulated_ids = []
  for simulated_trajectory in joint_scene.simulated_trajectories:
    # Check the length of each of the simulated fields.
    _raise_if_wrong_length(
        simulated_trajectory, 'center_x', config.n_simulation_steps
    )
    _raise_if_wrong_length(
        simulated_trajectory, 'center_y', config.n_simulation_steps
    )
    _raise_if_wrong_length(
        simulated_trajectory, 'center_z', config.n_simulation_steps
    )
    _raise_if_wrong_length(
        simulated_trajectory, 'heading', config.n_simulation_steps
    )
    # Check that each object ID is present in the original WOMD scenario.
    if simulated_trajectory.object_id not in sim_agent_ids:
      raise ValueError(
          f'Object {simulated_trajectory.object_id} is not a sim agent.'
      )
    simulated_ids.append(simulated_trajectory.object_id)
  # Check that all of the required objects/agents are simulated.
  missing_agents = set(sim_agent_ids) - set(simulated_ids)
  if missing_agents:
    raise ValueError(
        f'Sim agents {missing_agents} are missing from the simulation id'
        f' {simulated_ids}.'
    )


def validate_scenario_rollouts(
    scenario_rollouts: sim_agents_submission_pb2.ScenarioRollouts,
    original_scenario: scenario_pb2.Scenario,
    challenge_type: ChallengeType = ChallengeType.SIM_AGENTS,
) -> None:
  """Validates a `ScenarioRollouts` proto.

  Iteratively check that each `JointScene` is valid, while also checking if the
  number of scenes is correct.

  Args:
    scenario_rollouts: The `ScenarioRollouts` to be validated.
    original_scenario: The `Scenario` proto from which the simulation was
      started.
    challenge_type: The challenge type to use.

  Raises:
    ValueError if the `ScenarioRollouts` is invalid.
  """
  config = get_submission_config(challenge_type)
  if not scenario_rollouts.HasField('scenario_id'):
    raise ValueError('Missing `scenario_id` field.')
  if len(scenario_rollouts.joint_scenes) != config.n_rollouts:
    raise ValueError(
        'Incorrect number of parallel simulations. '
        f'(Actual: {len(scenario_rollouts.joint_scenes)}, '
        f'Expected: {config.n_rollouts})'
    )
  for joint_scene in scenario_rollouts.joint_scenes:
    validate_joint_scene(joint_scene, original_scenario, challenge_type)


def _raise_if_wrong_length(
    trajectory: sim_agents_submission_pb2.SimulatedTrajectory,
    field_name: str,
    expected_length: int,
) -> None:
  if len(getattr(trajectory, field_name)) != expected_length:
    raise ValueError(
        f'Invalid {field_name} tensor length '
        f'(actual: {len(getattr(trajectory, field_name))}, '
        f'expected: {expected_length})'
    )
