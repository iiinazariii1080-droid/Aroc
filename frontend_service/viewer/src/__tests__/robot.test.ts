/**
 * robot.test.ts — Unit tests for the Robot domain model.
 */
import { describe, it, expect } from 'vitest';
import { Robot } from '../domain/robot';

describe('Robot domain model', () => {
  it('starts with default 6-axis identity', () => {
    const robot = new Robot();
    expect(robot.axisCount).toBe(6);
    expect(robot.variant).toBe('6-6');
  });

  it('updateStatus with identity updates spec and variant', () => {
    const robot = new Robot();
    const result = robot.updateStatus({
      identity: { axis: 7, deviceType: 13, endEffector: 'gripper' },
    });

    expect(result.change.identity).toBe(true);
    expect(robot.variant).toBe('7-13');
    expect(robot.axisCount).toBe(7);
    expect(robot.identity.endEffector).toBe('gripper');
  });

  it('updateStatus with rawJoints normalizes and returns processed', () => {
    const robot = new Robot();
    robot.updateStatus({ identity: { axis: 6, deviceType: 6, endEffector: '' } });

    const result = robot.updateStatus({
      rawJoints: { angles: [10, 20, 30, 40, 50, 60, 70], timestamp: 1000 },
    });

    expect(result.change.joints).toBe(true);
    expect(result.processedJoints).toBeDefined();
    expect(result.processedJoints!.angles).toHaveLength(6); // trimmed to axis count
    expect(result.processedJoints!.timestamp).toBe(1000);
    expect(robot.jointsDeg).toEqual(result.processedJoints!.angles);
  });

  it('updateStatus with joints passes through without normalization', () => {
    const robot = new Robot();
    const result = robot.updateStatus({
      joints: { angles: [1, 2, 3, 4, 5, 6], timestamp: 2000 },
    });

    expect(result.processedJoints).toBeDefined();
    expect(result.processedJoints!.angles).toEqual([1, 2, 3, 4, 5, 6]);
  });

  it('updateStatus with rawLift stores motor units', () => {
    const robot = new Robot();
    const result = robot.updateStatus({
      rawLift: { motorUnits: 50000, timestamp: 3000 },
    });

    expect(result.change.lift).toBe(true);
    expect(result.processedLift).toEqual({ motorUnits: 50000, timestamp: 3000 });
    expect(robot.liftMotorUnits).toBe(50000);
  });

  it('updateStatus with mount updates mount state', () => {
    const robot = new Robot();
    const result = robot.updateStatus({
      mount: { tilt: 30, rotation: -45 },
    });

    expect(result.change.mount).toBe(true);
    expect(robot.mount).toEqual({ tilt: 30, rotation: -45 });
  });

  it('updateStatus with agvPose updates AGV state', () => {
    const robot = new Robot();
    const result = robot.updateStatus({
      agvPose: { x_m: 1.5, y_m: 2.0, theta_deg: 90, map_id: 1 },
    });

    expect(result.change.agvPose).toBe(true);
    expect(robot.agvPose.x_m).toBe(1.5);
    expect(robot.agvPose.theta_deg).toBe(90);
  });

  it('composite updateStatus processes all parts in one call', () => {
    const robot = new Robot();
    const result = robot.updateStatus({
      identity: { axis: 6, deviceType: 6, endEffector: '' },
      mount: { tilt: 10, rotation: 20 },
      rawJoints: { angles: [5, 10, 15, 20, 25, 30], timestamp: 5000 },
      rawLift: { motorUnits: 12000, timestamp: 5000 },
      agvPose: { x_m: 0.5, y_m: 0.5, theta_deg: 45, map_id: 2 },
    });

    expect(result.change.identity).toBe(true);
    expect(result.change.mount).toBe(true);
    expect(result.change.joints).toBe(true);
    expect(result.change.lift).toBe(true);
    expect(result.change.agvPose).toBe(true);
    expect(result.processedJoints!.angles).toEqual([5, 10, 15, 20, 25, 30]);
    expect(result.processedLift!.motorUnits).toBe(12000);
    expect(robot.liftMotorUnits).toBe(12000);
    expect(robot.agvPose.map_id).toBe(2);
  });

  it('change flags are false when field not present', () => {
    const robot = new Robot();
    const result = robot.updateStatus({
      mount: { tilt: 0, rotation: 0 },
    });

    expect(result.change.identity).toBe(false);
    expect(result.change.joints).toBe(false);
    expect(result.change.lift).toBe(false);
    expect(result.change.agvPose).toBe(false);
    expect(result.processedJoints).toBeNull();
    expect(result.processedLift).toBeNull();
  });
});
